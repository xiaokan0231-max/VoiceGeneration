# 克隆音色参考音频

每个克隆音色对应一个子目录，里面放参考音频 `ref.wav`，并在仓库根目录的
`voices.yaml` 里登记 `ref_text`（音频里实际说的话，必须一字不差）。

## 准备建议
- 单人说话，干净无背景噪音、无音乐
- 时长 5~15 秒（太短克隆不稳，太长无意义）
- 单声道，采样率 16k 或 24k，wav 格式最佳
- `ref_text` 与音频内容完全一致，包含标点

## 转换命令示例
```bash
ffmpeg -i 原始.m4a -ac 1 -ar 16000 voices/narrator_zh/ref.wav
```

目录里的 `narrator_zh/`、`narrator_ja/` 是占位，放入 `ref.wav` 并填好
`voices.yaml` 的 `ref_text` 后即可使用。
