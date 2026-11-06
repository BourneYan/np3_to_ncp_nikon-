# np3_to_ncp_nikon

把尼康云创下载的 **`.NP3`（Flexible Color Picture Control，版本 0310）** 配方转换为老款 Z 机身（如 **Z6II**）可用的经典 **`.NCP`（版本 0100）** 优化校准文件。

> 非官方工具，与尼康无关联。转换是有损近似，详见下方[限制](#限制)。

## 为什么需要转换

尼康云创的 `.NP3` 使用 *Flexible Color*，包含经典 `.NCP` 装不下的参数：

- 8 色 Color Blender（逐色相的色相 / 彩度 / 明度）
- 3 段 Color Grading（阴影 / 中间调 / 高光的分离色调）
- 清晰度、中等锐化

Z6II、Z5、Z6、Z7、Z50 等老款 Z 机身只支持经典 `.NCP`。

## 这个脚本做什么

- 解析 `.NP3` 的 tagged 记录（名称、底子、锐度、清晰度、色调滑块、Color Blender、Color Grading、自定义曲线）
- 写出合法的 **638 字节 `.NCP`（版本 0100，带 257 点自定义曲线）**
- 保留：
  - NP3 自带的自定义色调曲线；没有曲线时用对比度 / 高光 / 阴影 / 白色阶 / 黑色阶生成
  - Color Grading 的明度部分（默认 `--mode enhanced` 烘焙进曲线）
  - 饱和度（NP3 饱和度 + Blender/Grading 平均彩度估算）
  - 锐度
  - 黑白配方 → Monochrome NCP + 最接近的滤镜
- 生成可直接复制到 SD 卡的 `NIKON/CUSTOMPC/PICCONxx.NCP` 卡包
- 生成 CSV 转换报告

**脚本只使用 Python 标准库，没有第三方依赖。**

## 限制

NCP 的 1D 曲线无法表达逐色相偏移和分离色调：

- Color Blender 的逐色相色相 / 彩度变化无法保留
- Color Grading 的分离色调（例如阴影青、高光橙）无法保留
- 清晰度、中等锐化无法写入，载入后可在机内手动补偿

因此转换结果是**近似**，不是逐像素一致。对色彩要求高的配方，建议载入后在机内微调，或用 NX Studio 的“与照相机兼容”模式查看。

创意底子（`0x0801`–`0x0814`）在 NCP 里没有对应项；目前脚本只为 `0x0802`（Morning）内置了近似曲线，其他创意底子会退化为 Neutral 底子 + 原始色调参数。

## 安装 / 运行

需要 Python 3.10+。

```bash
# 直接运行（把 NP3 放到 ./NP3，输出到 ./NCP）
python3 np3_to_ncp.py

# 指定目录
python3 np3_to_ncp.py -i path/to/NP3 -o path/to/NCP

# 更保守：不把 Color Grading 明度/彩度烘焙进曲线
python3 np3_to_ncp.py --mode faithful

# 只生成 NCP，不生成卡包
python3 np3_to_ncp.py --no-card-pack
```

也可以 `pip install .` 后使用：

```bash
np3-to-ncp -i NP3 -o NCP
```

## 在 Z6II 上使用

1. 把生成的 `NIKON` 文件夹复制到 SD 卡根目录，确保路径为 `NIKON/CUSTOMPC/PICCONxx.NCP`
2. 相机：`照片拍摄菜单` → `管理优化校准` → `载入/保存` → `从卡载入`
3. 选择槽位载入（机内自定义槽位有限，可能需要分批）

## 输出

- `NCP/*.NCP`：按原文件名保存的转换结果
- `NIKON/CUSTOMPC/PICCON01.NCP` …：卡包
- `NCP/conversion_report.csv`：转换报告

## 版权与分发提醒

- 脚本可以自由发布；建议选择一个开源许可证（如 MIT）。
- `.NP3` 配方和转换后的 `.NCP` 预设可能包含尼康及配方创作者的版权/使用条款。公开发布前请确认你有权分发；更稳妥的做法是只发布脚本，让用户转换自己的文件。
- 本仓库不包含尼康官方封面图或配方原始文件。

## 参考

- [ssssota/nikon-flexible-color-picture-control](https://github.com/ssssota/nikon-flexible-color-picture-control) —— Flexible Color NP3 字段解析
- [MJ-best/np3-lab](https://github.com/MJ-best/np3-lab) —— 老款 Z 机身把 Flexible Color 转为 NCP 的思路
- [Pinksert/Nikon-Picture-Control-NCP-Analysis](https://github.com/Pinksert/Nikon-Picture-Control-NCP-Analysis) —— NCP / NP2 / NP3 字节布局与降级分析
- [tvgenious89/nikon-pc](https://github.com/tvgenious89/nikon-pc) —— NCP / NP3 编解码实现
