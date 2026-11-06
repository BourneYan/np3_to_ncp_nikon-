# NP3 → NCP（Z6II / 老款 Z 机身）

本目录中的 32 个 `.NCP` 文件由 `NP3/` 中的 32 个尼康云创 `.NP3` 配方转换而来。

- `NCP/`：按原文件名保存的转换结果
- `NIKON/CUSTOMPC/`：可直接复制到 SD 卡的卡包，文件名为 `PICCON01.NCP` … `PICCON32.NCP`
- `NCP/conversion_report.csv`：每个文件的转换记录（底子编号、是否带曲线、锐度、饱和度等）

## 为什么需要转换

尼康云创下载的 `.NP3` 是 **Flexible Color Picture Control**（版本 0310）。它包含：

- 8 色 Color Blender（每个色相的色相 / 彩度 / 明度）
- 3 段 Color Grading（阴影 / 中间调 / 高光的分离色调）
- 清晰度、中等锐化等新参数

Z6II 这类老款 Z 机身只能读取经典 `.NCP`（版本 0100）。NCP 没有 Color Blender / Color Grading / 清晰度 / 中等锐化的位置，因此转换必然是**有损**的。

## 保留了什么

- **色调曲线**：NP3 自带的自定义曲线会原样写入 NCP；没有曲线的配方则用对比度 / 高光 / 阴影 / 白色阶 / 黑色阶生成曲线。
- **Color Grading 的明度部分**：`--mode enhanced`（默认）会把阴影 / 中间调 / 高光的明度调整烘焙进 NCP 曲线。
- **饱和度**：由 NP3 饱和度、Color Blender 各色平均彩度、Color Grading 平均彩度估算。
- **锐度**：映射到 NCP 的锐化值（0–9）。
- **黑白配方**：转换为 Monochrome NCP，并选择最接近的滤镜。

## 无法保留什么

- Color Blender 的**逐色相**色相偏移和彩度变化
- Color Grading 的**分离色调**（例如阴影青、高光橙）
- 清晰度、中等锐化

这些在 NCP 的 1D 曲线里无法表达，所以转换结果只能做到“接近”，不能做到逐像素一致。载入后可以在机内手动补偿清晰度 / 中等锐化，但无法逐配方自动还原。对色彩要求高的配方，建议在机内再微调饱和度 / 色相，或用 NX Studio 的“与照相机兼容”模式查看。

## 关于 Bluegrain

`Bluegrain_jyota.NP3` 的底子是 `0x0802`（创意底子 Morning），而不是 Flexible Color 的 `0x0020`。NCP 不能选择创意底子，因此该文件使用 `Standard` 底子 + 一条近似 Morning 的提亮/柔和高光曲线来转换；结果只是近似，色相特征无法完整保留。

## 在 Z6II 上使用

1. 把 `NIKON` 文件夹复制到 SD 卡根目录（卡内路径应为 `NIKON/CUSTOMPC/PICCONxx.NCP`）。
2. 相机：`照片拍摄菜单` → `管理优化校准` → `载入/保存` → `从卡载入`。
3. 选择要载入的槽位。机内自定义优化校准槽位有限，需要分批载入。

> 注意：不同固件版本对文件名和槽位数量可能有细微差异。如果相机不识别，请先把卡格式化为相机使用的格式，再复制 `NIKON/CUSTOMPC`。

## 重新生成

在仓库根目录运行：

```bash
python3 tools/np3_to_ncp.py
```

生成更保守的版本（不把 Color Grading 明度/彩度烘焙进曲线）：

```bash
python3 tools/np3_to_ncp.py --mode faithful
```

只转换、不生成卡包：

```bash
python3 tools/np3_to_ncp.py --no-card-pack
```

脚本只使用 Python 标准库，不需要安装依赖。

目前脚本只为创意底子 `0x0802`（Morning）内置了近似曲线；如果以后加入其他创意底子的 NP3（例如 Pop / Bleach / Denim），它们会退化为 Neutral 底子 + 原始色调参数，需要时可在 `CREATIVE_CURVES` 中补充。

## 参考

- [ssssota/nikon-flexible-color-picture-control](https://github.com/ssssota/nikon-flexible-color-picture-control) —— Flexible Color NP3 字段解析
- [MJ-best/np3-lab](https://github.com/MJ-best/np3-lab) —— 老款 Z 机身（含 Z6II）将 Flexible Color  recipe 转为 NCP 的思路
- [Pinksert/Nikon-Picture-Control-NCP-Analysis](https://github.com/Pinksert/Nikon-Picture-Control-NCP-Analysis) —— NCP / NP2 / NP3 字节布局与降级分析
- [tvgenious89/nikon-pc](https://github.com/tvgenious89/nikon-pc) —— NCP / NP3 编解码实现
