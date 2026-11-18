# 编号 case 平均功耗绘图

从每个 `编号db.xlsx` 的首个 sheet 读取 **A 列时间戳、D 列功耗**，计算平均功耗，再按实验类别绘制折线图。图内列出各 case 的输入、权重、偏置和输出 shape。

## 运行

在本目录运行，需要 Python 3.10 或更新版本：

```powershell
python -m pip install -r requirements_power.txt
python -X utf8 plot_power.py --input "D:\功耗数据" --output "D:\平均功耗图"
```

`--input` 是数据根目录；`--output` 必须为空或不存在。省略输出参数会在输入目录下新建带时间戳的结果目录。表头没有功耗单位时，加 `--power-unit W` 或 `--power-unit mW`，按实际单位填写。

## 输入目录与编号

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

每个算子单独从 0 编号，支持旧的 `closed_*` 目录名。配套 `power_model_index.json` 包含内源完整类别的 156 个配置，默认来源为内源；映射见 [numbering.csv](numbering.csv)。只放已测文件即可。

仅读取完整匹配 `编号db.xlsx` 的文件；兼容 `a1db.xlsx`、`001_db.xlsx` 及大小写变体。普通 Excel 和 `~$` 临时文件忽略。

已知算子目录中的 `run01/0db.xlsx`、`run02/0db.xlsx` 合并为同一 case 的重复测量。未知目录按直接存放数据的文件夹独立分组；同名但路径不同的文件夹分别处理。未知目录不推测原编号、shape 或计算量，横轴按 case 自然顺序排列。

## 统计与图形

默认计算每个文件全部有效采样的算术均值，多轮文件的均值再等权平均；误差棒表示多轮均值的最小至最大范围。无效采样不补零，不自动裁剪启动/空闲区间，不扣基线。

- `--average time`：改用梯形积分除以时间跨度的时间加权均值。时间戳或采样存在问题时明确报错，不替代为普通均值。
- `--x-axis scan`：默认按原实验扫描维度绘图；`flops` / `bytes` 分别使用计算量或逻辑数据移动量，缺失或恒定时退回扫描轴。
- `--source internal/external`：显式指定来源；默认识别目录标记，未标记时采用配套索引的内源默认值。

图下方逐项列出公式，例如：

```text
case 0 (g1): A常量[512,4096] @ B[4096,16] -> Y[512,16]
```

`@` 为矩阵乘法，`*` 为逐元素乘法，箭头指向输出。标为“常量”的项是权重、偏置等固定参数；Gemm、卷积等使用函数式写法，不根据 shape 猜测转置、alpha/beta 或 padding。历史 Gemm 数值校验失败保留标记，未知目录显示“运算与shape未知”。

## 输出

- `index.html`、`figures/*.png` 和 `*.svg`：浏览全部实验类别的图。
- `power_summary.csv`、`power_runs.csv`：配置汇总和逐文件统计，统一为 mW。
- `coverage.csv`：已知配置及发现的未知 case 的测量覆盖情况。
- `errors.csv`、`run_info.json`：读取错误、分组及运行参数。其他有效文件会继续处理。

CSV 为 UTF-8 BOM，可直接用 Excel 打开。已有合成数据与布局验证；公司真实测量应在本地核对单位和采样区间。

```powershell
python -m unittest test_plot_power -v
```
