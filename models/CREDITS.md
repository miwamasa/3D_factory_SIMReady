# 3Dモデルと表示ライブラリのクレジット

搬送ローラーコンベヤと青い部品箱の原作者は **NVIDIA SimReady Assets** です。下記の各アセットの配布メタデータに `CC-BY-4.0` の記載があります。[NVIDIA SimReady Assets](https://huggingface.co/datasets/nvidia/simready-assets) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) · [同梱ライセンス全文](NVIDIA_SIMREADY_LICENSE.txt)

| アセット | ラインでの用途 | 配布元 |
|---|---|---|
| `conveyor_belt_a08` | S01〜S06をつなぐ直線ローラーコンベヤ | [配布パッケージ](https://huggingface.co/datasets/nvidia/simready-assets/tree/653d91e182011f357cf1644013cc94ddf194d349/conveyor_belt_a08) |
| `crate_blue_a01` | 部材供給と検査後の青い部品箱 | [配布パッケージ](https://huggingface.co/datasets/nvidia/simready-assets/tree/653d91e182011f357cf1644013cc94ddf194d349/crate_blue_a01) |

上記アセットをUSDからBlenderへ取り込み、ラインに合わせた寸法調整、ポリゴン削減、簡易材質への変更、配置・複製、GLB変換を行いました。巻線機、圧入機、組付けアーム、締付機、検査設備、モーター、建屋と床標示はこのテストベンチ用に作成した形状です。

ブラウザ描画には **Three.js r160** と同版の `GLTFLoader`、`OrbitControls`、`BufferGeometryUtils` を使用します。モジュールは同梱し、補助モジュールのimport先を相対パスに変更しました。[Three.js r160](https://github.com/mrdoob/three.js/tree/r160) · [MITライセンス](../static/factory3d/vendor/LICENSE.txt)
