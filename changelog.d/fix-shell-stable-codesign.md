type: fixed
- **看板壳改用稳定自签证书签名，TCC 授权不再每次更新就失效**：`shell/build.sh` 此前用 ad-hoc 签名（`-s -`），代码身份 = 二进制 cdhash、每次构建都变，所以每跑一次 `install.sh` macOS 就把壳当成一个陌生 app——系统设置里「屏幕录制」开关明明开着，抓屏却一次次重弹授权框（#316）。现在与 `mac/build.sh` 用同一张 `Zelin AI Engineer Dev`：keychain 里认出就用它签，签名指纹跨版本不变，屏幕录制 / 麦克风 / 自动化 / 笔记库授权跨更新存活。**一次性过渡**：换身份的那一次这四项会各再弹一次（做法见 `docs/TROUBLESHOOTING.md`「换壳后的 TCC 重授权」）。
  没装证书的机器照旧 ad-hoc（跑一次 `bash mac/scripts/make-signing-cert.sh` 生成并导入即可）；codesign 本身跑在 60 s 墙钟里，万一 keychain 弹出没人能点的授权框也会超时回落 ad-hoc，而不是把自动部署的 `ui` 步拖到预算耗尽而失败。
