import * as THREE from './vendor/three.module.min.js';
import { OrbitControls } from './vendor/controls/OrbitControls.js';
import { GLTFLoader } from './vendor/loaders/GLTFLoader.js';

const STATION_NAMES = ['部材供給', 'ステータ巻線', '回転子・軸受', 'ハウジング組付', 'ボルト締付', 'EOL検査'];
const STATION_IDS = STATION_NAMES.map((_, index) => `S0${index + 1}`);
const COLORS = { run: 0x72d8a4, wait: 0xf1bd59, fault: 0xff6669, idle: 0x8598a6 };
const PROCESS = ['治具にステータを載せる', '巻線ヘッドが回り、銅線が加わる', '軸受と回転子を押し込む',
  'アームが外装を運び、軸方向に組み付ける', '工具が降下し、ボルトを締める', '完成品を回して検査する'];
const smooth = THREE.MathUtils.smoothstep;
const mix = THREE.MathUtils.lerp;
const clamp = THREE.MathUtils.clamp;
const workStroke = p => smooth(p, 0.22, 0.42) * (1 - smooth(p, 0.68, 0.82));

function motorParts(object) {
  return Object.fromEntries(['STATOR', 'WINDING', 'ROTOR', 'HOUSING', 'FASTENERS']
    .map(name => [name.toLowerCase(), object.getObjectByName(`MOTOR_${name}`)]));
}

function poseMotor(part, station, p) {
  const { stator, winding, rotor, housing, fasteners } = part;
  const turns = station === 1 ? smooth(p, 0.31, 0.68) : Number(station > 1);
  stator.position.y = 0.37 + (station === 0 ? 0.22 * smooth(p, 0.18, 0.27) * (1 - smooth(p, 0.32, 0.51)) : 0);
  stator.visible = station > 0 || p >= 0.22;
  winding.children.forEach((sector, i) => { sector.visible = turns > i / winding.children.length; });
  rotor.visible = station > 2 || (station === 2 && p >= 0.32);
  rotor.position.x = station === 2 ? 0.44 * (1 - smooth(p, 0.32, 0.63)) : 0;
  rotor.rotation.x = station === 5 ? 5 * Math.PI * smooth(p, 0.34, 0.76) : 0;
  housing.visible = station > 3 || (station === 3 && p >= 0.70);
  const tightened = station === 4 ? smooth(p, 0.42, 0.70) : Number(station > 4);
  fasteners.children.forEach((bolt, i) => { bolt.visible = tightened > i / fasteners.children.length; });
}

// Wrist targets in the station's coordinate frame, ordered by operation phase.
const ARM_PATH = [
  [0, 0.08, 2.25, -1.62], [0.19, 0.66, 2.16, -2.12], [0.30, 0.66, 1.91, -2.12],
  [0.41, 0.66, 2.19, -2.12], [0.52, 0.66, 2.19, -1.60], [0.60, 0.66, 1.82, -1.60],
  [0.70, 0, 1.82, -1.60], [0.82, 0, 2.23, -1.60], [1, 0.08, 2.25, -1.62],
];

function poseArm(arm, p) {
  const index = ARM_PATH.findIndex(key => key[0] >= p && key[0] > 0);
  const [start, end] = [ARM_PATH[index - 1], ARM_PATH[index]];
  const t = smooth(p, start[0], end[0]);
  const x = mix(start[1], end[1], t) - arm.base.position.x;
  const y = mix(start[2], end[2], t) - arm.base.position.y;
  const z = mix(start[3], end[3], t) - arm.base.position.z;
  const distance = Math.hypot(x, z);
  const upper = arm.elbow.position.x;
  const lower = arm.wrist.position.x;
  const bend = -Math.acos(clamp((distance ** 2 + y ** 2 - upper ** 2 - lower ** 2) / (2 * upper * lower), -1, 1));
  const lift = Math.atan2(y, distance) - Math.atan2(lower * Math.sin(bend), upper + lower * Math.cos(bend));
  arm.base.rotation.y = Math.atan2(-z, x);
  arm.shoulder.rotation.set(0, 0, lift);
  arm.elbow.rotation.set(0, 0, bend);
  arm.wrist.rotation.set(0, 0, -lift - bend);
  arm.spare.visible = p < 0.30;
  arm.payload.visible = p >= 0.30 && p < 0.70;
}

function poseMachine(rig, station, p) {
  if (station === 3) {
    poseArm(rig, p);
    return;
  }
  const { tool, rest } = rig;
  const stroke = workStroke(p);
  tool.position.copy(rest);
  if (station === 0) {
    tool.position.y -= 0.13 * stroke;
    tool.position.z -= 0.39 * (1 - smooth(p, 0.28, 0.48)) * smooth(p, 0, 0.18);
  } else if (station === 1) {
    tool.position.x += 0.24 * stroke;
    tool.rotation.x = 5 * Math.PI * smooth(p, 0.30, 0.68);
  } else if (station === 2) {
    tool.position.x -= 0.33 * (smooth(p, 0.31, 0.63) - smooth(p, 0.67, 0.80));
  } else if (station === 4) {
    tool.position.y -= 0.20 * stroke;
    tool.rotation.y = 5 * Math.PI * smooth(p, 0.41, 0.70);
  } else {
    tool.position.x -= 0.12 * stroke;
    tool.rotation.x = 5 * Math.PI * smooth(p, 0.34, 0.76);
    rig.trace.forEach((bar, i) => {
      const height = rig.heights[i] * (0.32 + stroke * (0.55 + 0.30 * Math.sin(p * 37 + i * 1.2)));
      bar.scale.y = height;
      bar.position.y = 1.80 + height / 2;
    });
  }
}

function timeLabel(seconds) {
  return [Math.floor(seconds / 3600), Math.floor(seconds % 3600 / 60), seconds % 60]
    .map(number => String(number).padStart(2, '0')).join(':');
}

function stationState(snapshot, index) {
  const station = snapshot.stations[index];
  const unit = station.queue[0];
  if (station.fault) return { tone: 'fault', label: '異常停止' };
  if (station.id === 'S05' && snapshot.scenario === 'torque_drift') return { tone: 'wait', label: '工具ドリフト' };
  if (!snapshot.running) return { tone: 'idle', label: 'ライン停止' };
  if (unit && unit.elapsed >= station.cycle && snapshot.stations[index + 1]?.queue.length >= 3) {
    return { tone: 'wait', label: '搬送待ち' };
  }
  return unit ? { tone: 'run', label: '加工中' } : { tone: 'idle', label: '待機中' };
}

export function mountFactoryView(root, actions) {
  const element = name => root.querySelector(`#factory3d-${name}`);
  const stage = element('stage');
  const loading = element('loading');
  const labels = element('labels');
  const selectedDetail = element('detail');
  const canvasScene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-14, 14, 7, -7, 0.1, 130);
  const lookAt = new THREE.Vector3(0, 0.4, -0.25);
  const anchor = new THREE.Vector3();
  const selectedBoxes = new Map();
  const parts = new Map();
  const stageNodes = new Map();
  const lamps = new Map();
  const rigs = [];
  const liveParts = new THREE.Group();
  canvasScene.add(liveParts);
  let snapshot = null;
  let supervisor = false;
  let online = false;
  let busy = '';
  let lastCommand = '';
  let selected = 0;
  let renderer;
  let controls;
  let template;
  let demoPart;
  let demo = false;
  let demoSeconds = 0;
  let cameraMode = 'overview';
  let currentFrame = 0;
  let viewportWidth = 0;
  let viewportHeight = 0;
  let needsRender = true;

  const stationButtons = STATION_IDS.map((id, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'factory3d-station-button';
    button.textContent = `${id} ${STATION_NAMES[index]}`;
    button.addEventListener('click', () => selectStation(index));
    element('stations').append(button);
    const pin = document.createElement('button');
    pin.type = 'button';
    pin.className = 'factory3d-pin';
    pin.textContent = id;
    pin.setAttribute('aria-label', `${id} ${STATION_NAMES[index]} を選択`);
    pin.hidden = true;
    pin.addEventListener('click', () => selectStation(index));
    labels.append(pin);
    return { button, pin };
  });

  function selectStation(index) {
    selected = index;
    stationButtons.forEach(({ button, pin }, current) => {
      button.setAttribute('aria-pressed', String(current === selected));
      pin.setAttribute('aria-pressed', String(current === selected));
    });
    for (const [id, box] of selectedBoxes) box.visible = id === STATION_IDS[selected];
    demoSeconds = 0;
    if (cameraMode === 'close') setCamera('close');
    renderStation();
    needsRender = true;
  }

  function renderStation() {
    element('station-id').textContent = STATION_IDS[selected];
    element('process').textContent = PROCESS[selected];
    if (!snapshot) return;
    const station = snapshot.stations[selected];
    const state = stationState(snapshot, selected);
    selectedDetail.replaceChildren();
    const title = document.createElement('strong');
    title.textContent = station.name;
    selectedDetail.append(title);
    const status = document.createElement('p');
    status.className = 'factory3d-stage-state';
    status.dataset.tone = state.tone;
    status.textContent = state.label;
    selectedDetail.append(status);
    const values = document.createElement('dl');
    for (const [label, value] of [
      ['仕掛品', `${station.queue.length} / 3 台`],
      ['サイクル', `${station.cycle} 秒`],
      ['温度', `${station.temperature.toFixed(1)} ℃`],
      ['累積処理', `${station.completed} 台`],
    ]) {
      const key = document.createElement('dt');
      const cell = document.createElement('dd');
      key.textContent = label;
      cell.textContent = value;
      values.append(key, cell);
    }
    selectedDetail.append(values);
    const queue = document.createElement('ol');
    queue.className = 'factory3d-queue';
    for (const [index, unit] of station.queue.entries()) {
      const row = document.createElement('li');
      const serial = `M${String(unit.serial).padStart(5, '0')}`;
      const progress = index ? '待機' : `${Math.round(unit.elapsed / station.cycle * 100)}%`;
      row.textContent = `${serial} · ${unit.order} · ${progress}`;
      queue.append(row);
    }
    if (!queue.childNodes.length) {
      const empty = document.createElement('li');
      empty.textContent = '仕掛品なし';
      queue.append(empty);
    }
    selectedDetail.append(queue);
  }

  function setCamera(mode = 'overview') {
    if (!controls) return;
    cameraMode = mode;
    camera.zoom = 1;
    controls.target.copy(lookAt);
    if (mode === 'close') {
      const x = stageNodes.get(STATION_IDS[selected]).position.x;
      controls.target.set(x, 1.39, -1.02);
      camera.position.set(x + 3.9, 4.1, 5.2);
      camera.zoom = 3.8;
    } else {
      camera.position.set(...(mode === 'top' ? [0, 33, 0.08] : [9.4, 15.8, 23]));
    }
    camera.updateProjectionMatrix();
    controls.update();
    needsRender = true;
  }

  function resize() {
    if (!renderer || !stage.clientWidth || !stage.clientHeight) return;
    viewportWidth = stage.clientWidth;
    viewportHeight = stage.clientHeight;
    const aspect = viewportWidth / viewportHeight;
    const halfHeight = Math.max(5.7, 13.35 / aspect);
    camera.left = -halfHeight * aspect;
    camera.right = halfHeight * aspect;
    camera.top = halfHeight;
    camera.bottom = -halfHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(viewportWidth, viewportHeight, false);
    needsRender = true;
  }

  function newPart(parent) {
    const obj = template.clone(true);
    obj.visible = true;
    obj.traverse(child => { child.castShadow = false; });
    parent.add(obj);
    return { obj, motor: motorParts(obj) };
  }

  function placePart(part, station, p, queueIndex) {
    const node = stageNodes.get(STATION_IDS[station]);
    const shuttle = queueIndex ? 0 : smooth(p, 0.04, 0.20) * (1 - smooth(p, 0.82, 0.97));
    const travel = queueIndex ? -0.84 * queueIndex : mix(-0.15, 0.65, p);
    part.target.set(node.position.x + travel * (1 - shuttle),
      mix(template.position.y, node.userData.fixture_height, shuttle), -node.userData.fixture_y * shuttle);
  }

  function syncScene(snapToTarget) {
    if (!template || !snapshot) return;
    const currentSerials = new Set();
    snapshot.stations.forEach((station, index) => {
      const state = stationState(snapshot, index);
      const lamp = lamps.get(station.id);
      if (lamp) {
        lamp.color.setHex(COLORS[state.tone]);
        lamp.emissive.setHex(COLORS[state.tone]);
        lamp.emissiveIntensity = state.tone === 'idle' ? 0.08 : 0.65;
      }
      station.queue.forEach((unit, queueIndex) => {
        currentSerials.add(unit.serial);
        const fraction = queueIndex ? 0 : clamp(unit.elapsed / station.cycle, 0, 1);
        let part = parts.get(unit.serial);
        const snap = !part || snapToTarget;
        if (!part) {
          part = { ...newPart(liveParts), target: new THREE.Vector3() };
          parts.set(unit.serial, part);
        }
        if (snap || part.station !== index) part.phase = fraction;
        part.station = index;
        part.queueIndex = queueIndex;
        part.fraction = fraction;
        if (snap) {
          placePart(part, index, fraction, queueIndex);
          part.obj.position.copy(part.target);
        }
      });
    });
    for (const [serial, part] of parts) {
      if (!currentSerials.has(serial)) {
        liveParts.remove(part.obj);
        parts.delete(serial);
      }
    }
    needsRender = true;
  }

  function toggleDemo() {
    demo = !demo;
    demoSeconds = 0;
    liveParts.visible = !demo;
    demoPart.obj.visible = demo;
    element('demo').setAttribute('aria-pressed', String(demo));
    element('demo-note').hidden = !demo;
    if (demo) setCamera('close');
    needsRender = true;
  }

  function paintLabels() {
    for (const [index, id] of STATION_IDS.entries()) {
      const pin = stationButtons[index].pin;
      const node = stageNodes.get(id);
      if (!node) continue;
      node.getWorldPosition(anchor);
      anchor.y += 3.45;
      anchor.z -= 0.65;
      anchor.project(camera);
      const x = (anchor.x + 1) * 0.5 * viewportWidth;
      const y = (1 - anchor.y) * 0.5 * viewportHeight;
      pin.hidden = x < 26 || x > viewportWidth - 26 || y < 18 || y > viewportHeight - 18 || anchor.z > 1;
      pin.style.left = `${x}px`;
      pin.style.top = `${y}px`;
    }
  }

  function animate(now) {
    requestAnimationFrame(animate);
    if (root.classList.contains('hidden') || !viewportWidth || document.hidden) {
      currentFrame = now;
      return;
    }
    const dt = Math.min(0.10, (now - currentFrame) / 1000);
    currentFrame = now;
    const cameraMoved = controls.update();
    let partMoved = demo;
    const phases = [0, 0, 0, 0, 0, 0];
    if (demo) {
      demoSeconds = (demoSeconds + dt) % 8;
      const phase = demoSeconds / 8;
      phases[selected] = phase;
      placePart(demoPart, selected, phase, 0);
      demoPart.obj.position.copy(demoPart.target);
      poseMotor(demoPart.motor, selected, phase);
    } else {
      const easing = 1 - Math.exp(-8 * dt);
      for (const part of parts.values()) {
        const { obj, station, fraction, queueIndex } = part;
        if (!online || !snapshot.clock_running || !snapshot.running || snapshot.stations[station].fault) {
          part.phase = fraction;
        } else {
          const change = fraction - part.phase;
          part.phase = Math.abs(change) < 0.0005 ? fraction : part.phase + change * easing;
          partMoved ||= part.phase !== fraction;
        }
        if (!queueIndex) phases[station] = part.phase;
        placePart(part, station, part.phase, queueIndex);
        if (obj.position.distanceToSquared(part.target) > 0.000001) {
          obj.position.lerp(part.target, easing);
          partMoved = true;
        } else {
          obj.position.copy(part.target);
        }
        poseMotor(part.motor, station, part.phase);
      }
    }
    if (!needsRender && !cameraMoved && !partMoved) return;
    rigs.forEach((rig, index) => poseMachine(rig, index, phases[index]));
    renderer.render(canvasScene, camera);
    paintLabels();
    needsRender = false;
  }

  async function loadModel() {
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.6));
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.07;
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      renderer.shadowMap.autoUpdate = false;
      renderer.domElement.setAttribute('aria-label', 'モーター組立ラインの3Dモデル');
      stage.prepend(renderer.domElement);
      canvasScene.background = new THREE.Color(0x14222e);
      canvasScene.add(new THREE.HemisphereLight(0xe4f5ff, 0x67727b, 2.0));
      const key = new THREE.DirectionalLight(0xfff4de, 2.3);
      key.position.set(-3, 13, 9);
      key.castShadow = true;
      key.shadow.mapSize.set(2048, 2048);
      Object.assign(key.shadow.camera, { left: -17, right: 17, top: 10, bottom: -10, far: 45 });
      key.shadow.bias = -0.00025;
      key.shadow.normalBias = 0.025;
      canvasScene.add(key);
      controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.minZoom = 0.72;
      controls.maxZoom = 5;
      controls.minPolarAngle = 0.025;
      controls.maxPolarAngle = Math.PI * 0.48;
      setCamera();
      new ResizeObserver(resize).observe(stage);
      resize();
      const { scene: model } = await new GLTFLoader().loadAsync('/factory3d/line.glb');
      for (const id of STATION_IDS) {
        const node = model.getObjectByName(id);
        if (!node) throw new Error(`GLBに${id}が見つかりません`);
        stageNodes.set(id, node);
        const lamp = model.getObjectByName(`${id}_LAMP`);
        if (lamp?.isMesh) {
          lamp.material = lamp.material.clone();
          lamps.set(id, lamp.material);
        }
        const outline = new THREE.BoxHelper(node, 0x9ad8ea);
        outline.visible = false;
        selectedBoxes.set(id, outline);
        canvasScene.add(outline);
      }
      template = model.getObjectByName('MOTOR_TEMPLATE');
      if (!template) throw new Error('GLBに仕掛品モデルが見つかりません');
      template.visible = false;
      const named = name => {
        const node = model.getObjectByName(name);
        if (!node) throw new Error(`GLBに${name}が見つかりません`);
        return node;
      };
      STATION_IDS.forEach((id, index) => {
        if (index === 3) {
          const arm = Object.fromEntries(['BASE', 'SHOULDER', 'ELBOW', 'WRIST', 'SPARE']
            .map(name => [name.toLowerCase(), named(`${id}_${name}`)]));
          arm.payload = arm.spare.clone(true);
          arm.payload.position.set(0, -0.43, 0);
          arm.wrist.add(arm.payload);
          rigs.push(arm);
        } else {
          const tool = named(`${id}_TOOL`);
          const trace = index === 5 ? Array.from({ length: 8 }, (_, i) => named(`${id}_TRACE_${i}`)) : [];
          rigs.push({ tool, rest: tool.position.clone(), trace, heights: trace.map(bar => bar.scale.y) });
        }
      });
      rigs.forEach((rig, index) => poseMachine(rig, index, 0));
      model.traverse(node => {
        if (node.isMesh) {
          const moving = rigs.some(rig => {
            const movable = rig.tool ?? rig.base;
            return movable.getObjectById(node.id) || rig.spare?.getObjectById(node.id);
          });
          node.castShadow = !moving;
          node.receiveShadow = true;
        }
      });
      demoPart = { ...newPart(canvasScene), target: new THREE.Vector3() };
      demoPart.obj.visible = false;
      canvasScene.add(model);
      renderer.shadowMap.needsUpdate = true;
      selectStation(selected);
      syncScene(true);
      loading.hidden = true;
      element('close').disabled = element('demo').disabled = false;
      currentFrame = performance.now();
      requestAnimationFrame(animate);
    } catch (error) {
      loading.textContent = `3D表示を開始できませんでした。${error.message}`;
      renderer?.dispose();
      renderer?.domElement.remove();
    }
  }

  function renderControls() {
    const canControl = Boolean(snapshot && supervisor && online && !busy);
    element('jump').disabled = !canControl;
    element('clock').disabled = !canControl;
    element('seconds').disabled = !canControl;
    element('clock').textContent = snapshot?.clock_running ? '時計を停止' : '時計を再開';
    element('jump-note').textContent = busy ? 'シミュレーションを更新中…' : !supervisor ? '現場責任者のキーで時間を操作できます。' : lastCommand || '選んだ秒数だけ仮想時間を進めます。';
  }

  async function changeTime(operation) {
    if (!supervisor || !online || busy || !snapshot) return;
    busy = operation;
    renderControls();
    try {
      if (operation === 'jump') {
        const seconds = Number(element('seconds').value);
        const tick = await actions.jump(seconds);
        lastCommand = `+${seconds}秒 · ${timeLabel(tick)}`;
      } else {
        const running = !snapshot.clock_running;
        await actions.clock(running);
        lastCommand = running ? '時計を再開しました。' : '時計を停止しました。';
      }
    } catch (error) {
      lastCommand = error.message;
    } finally {
      busy = '';
      renderControls();
    }
  }

  element('jump').addEventListener('click', () => changeTime('jump'));
  element('clock').addEventListener('click', () => changeTime('clock'));
  element('overview').addEventListener('click', () => setCamera());
  element('top').addEventListener('click', () => setCamera('top'));
  element('close').addEventListener('click', () => setCamera('close'));
  element('demo').addEventListener('click', toggleDemo);
  selectStation(0);
  loadModel();

  return {
    update(next, connection) {
      supervisor = connection.supervisor;
      online = connection.online;
      element('status').textContent = online ? '● LIVE / SIMULATOR' : next ? '● 最終受信値' : '接続待ち';
      element('status').dataset.online = String(online);
      const previous = snapshot;
      snapshot = next;
      renderControls();
      if (!snapshot || previous === snapshot) return;
      const { kpi } = snapshot;
      const measured = kpi.lead_time_sample_count ?? 0;
      element('rate').textContent = kpi.output_rate_per_hour == null ? '—' : `${kpi.output_rate_per_hour.toFixed(1)} 台/時`;
      element('rate-detail').textContent = `直近 ${kpi.output_window_s ?? 0} 仮想秒の検査完了数`;
      element('wip').textContent = `${kpi.wip} 台`;
      element('lead').textContent = measured ? `${kpi.lead_time_avg_s.toFixed(1)} 秒` : '—';
      element('lead-detail').textContent = measured ? `投入 → 検査完了 · 計測 ${measured} 台` : '投入 → 検査完了 · 計測待ち';
      element('time').textContent = timeLabel(snapshot.tick);
      element('clock-status').textContent = `${snapshot.clock_running ? '時計進行中' : '時計停止'} · ${snapshot.speed}× · ライン${snapshot.running ? '稼働中' : '停止'}`;
      snapshot.stations.forEach((station, index) => {
        const state = stationState(snapshot, index);
        const { button, pin } = stationButtons[index];
        button.dataset.tone = pin.dataset.tone = state.tone;
        pin.textContent = `${station.id} · ${station.queue.length}`;
      });
      renderStation();
      syncScene(busy === 'jump' || Boolean(previous && snapshot.tick < previous.tick));
    },
  };
}
