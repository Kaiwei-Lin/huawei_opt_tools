# 每个 case 的功耗波形

每种算子输出一张画布，每个已测 case 占一个子图，标注编号、实验类型及输入、权重、偏置和输出 shape。数据来自 `编号db.xlsx` 首个 sheet 的 **A 列时间戳及五路功耗**。

| Excel 列 | 功耗通道 |
| --- | --- |
| D（第 4 列） | NPU |
| Y（第 25 列） | Vbuck8_1v8 |
| AB（第 28 列） | VBUCK1_1V12 |
| AE（第 31 列） | VBUCK0_PERI |
| AN（第 40 列） | DDRPHY |

每个子图同时显示五路曲线，颜色在所有图中保持一致；重复轮次用线型区分。图例标明各通道名称、列字母及平均功耗（多轮均值等权平均）。标题颜色仍表示实验类型。

## 运行

在本目录运行，需要 Python 3.10 或更新版本：

```powershell
python -m pip install -r requirements_power.txt
python -X utf8 plot_power_waveforms.py --input "D:\功耗数据" --output "D:\功耗波形"
```

输出目录必须为空或不存在。必须保留配套的 `plot_power.py` 和 `power_model_index.json`；该目录可独立复制到公司电脑使用。

默认分别从各功耗列的表头识别 W/mW，再统一为 mW。若表头没有单位，按真实数据指定，例如 A 列为微秒、所有所选功耗列为瓦：

```powershell
python -X utf8 plot_power_waveforms.py --input "D:\功耗数据" --output "D:\功耗波形" --input-time-unit us --power-unit W
```

运行命令不变，默认读取五路。只看部分通道时加 `--channels D AN`；只看 NPU 或处理仅有第四列功耗的旧文件时用 `--channels D`。某一路缺列、无有效数据或单位不明会单独记录到 `errors.csv`，其他有效通道继续绘制。

## 数据排布

```text
功耗数据/
├── convolution_close/
│   ├── 0db.xlsx
│   └── 1db.xlsx
├── matmul_close/
│   └── 0db.xlsx
└── 补充实验/
    ├── 0db.xlsx
    └── 2db.xlsx
```

每个算子分别从 0 编号，兼容旧的 `closed_*` 名称。配套索引含 156 个内源完整类别配置，详见 [numbering.csv](numbering.csv)。默认按内源处理，只绘制发现的 case。

仅读取完整匹配 `编号db.xlsx` 的文件；兼容 `a1db.xlsx`、`001_db.xlsx` 及大小写变体。普通 Excel 和 `~$` 临时文件忽略。未知目录按直接存放数据的文件夹归为同一种实验类型，单独画图；不同路径不混合，缺少的模型 shape 标为未知。

已知算子目录下的 `run01/0db.xlsx`、`run02/0db.xlsx` 会在同一子图叠加，用不同线型表示轮次。未知目录的不同子目录各自成组，不自动推断为重复测量。

## 图形标注与显示

子图标题含 case 编号、原编号、实验类型、扫描参数和完整运算 shape，例如：

```text
A常量[512,4096] @ B[4096,16] -> Y[512,16]
Conv(X[1,64,2,8192], W常量[128,64,1,1], B常量[128]) -> Y[1,128,2,8192]
```

`@` 为矩阵乘法，`*` 为逐元素乘法，箭头指向输出；“常量”表示权重、偏置等固定参数。Gemm、卷积等采用函数式写法，不根据 shape 猜测未记录的算子属性。Gemm 的历史数值校验失败仍保留标记。

| 参数 | 作用 |
| --- | --- |
| `--cols 4` | 默认每行 4 个子图，可设 1–8 |
| `--independent-y` | 每个 case 独立功耗刻度；默认同算子统一刻度 |
| `--input-time-unit s/ms/us/ns` | 数值时间戳的原始单位 |
| `--time-unit s/ms/us` | 图上的时间显示单位，默认 ms |
| `--channels D Y AB AE AN` | 默认五路；可选择任意子集，例如 `D AN` |
| `--power-unit W/mW` | 覆盖所有所选功耗列的原始单位，输出统一为 mW；省略时逐列识别表头 |
| `--max-points 4000` | 长波形分段保留端点、峰谷及缺失边界；0 表示全部采样 |
| `--font-file 路径` | 指定中文字体；Windows 默认自动使用微软雅黑等字体 |

各文件起始时间归零，同一文件的五路共用时间轴；某一路开头缺失不会改变其时间原点。各子图按实际记录时长显示。缺失功耗在对应通道保留断线，不影响其他通道；时间戳重复、倒序或中途无效时记录文件错误，不自动排序。各路独立统计，不相加，也不做平滑、基线扣除或自动裁剪。显示点数缩减不影响全量采样统计。

## 输出

- `index.html`：查看所有算子画布和原尺寸图片。
- `figures/*.png`、`*.svg`：每个算子或未知文件夹的一张完整画布。
- `waveform_summary.csv`：每个文件/通道一行，含“功耗通道”“功耗列”、采样数、均值、极值、时长和完整 shape，CSV 为 UTF-8 BOM。
- `errors.csv`、`run_info.json`：错误、各 case 的公式和运行参数。

`valid_files` 按文件去重，`valid_file_channels` 为成功的文件与通道组合数；部分通道失败的文件会同时计入有效文件和失败文件。部分失败时退出码为 2，其他结果仍可查看。

已用合成数据验证多通道读取、单位换算、波形与图例及 shape 标注；公司真实功耗文件需在本地核对。

```powershell
python -m unittest test_plot_power test_plot_power_waveforms -v
```
