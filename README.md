# Huawei NPU 模型与功耗工具集

将模型生成、子图分析和功耗绘图工具放在同一个仓库中。每个工具有独立目录、依赖清单和使用说明；先进入相应目录再运行命令。

| 目录 | 用途 | 主要入口 |
| --- | --- | --- |
| [onnx_omc_generator](onnx_omc_generator/README.md) | 配置单算子的 shape、权重和属性，生成 ONNX，调用 OMG 编译 OMC，并进行时延实验 | `generate_ops.py`、`profile_ops.py` |
| [op_power_subgraph_extract](op_power_subgraph_extract/README.md) | ONNX/OMC 图分析、计算量统计、子图筛选与导出、去重、功耗合并及 DDR/吞吐图 | `python -m onnx_graph` 及包内各模块 |
| [power_plot](power_plot/README.md) | 从编号功耗文件计算平均功耗，按实验类别画折线图，标注运算公式和完整 shape | `plot_power.py` |
| [power_waveforms](power_waveforms/README.md) | 每种算子一张画布，每个 case 一个波形子图，标注实验类型、公式及 shape | `plot_power_waveforms.py` |

```text
huawei_opt_tools/
├── onnx_omc_generator/
├── op_power_subgraph_extract/
├── power_plot/
├── power_waveforms/
└── SOURCES.json
```

## 获取与运行

需要 Python 3.10 或更新版本。建议使用虚拟环境；各工具的依赖按需安装。

```powershell
git clone https://github.com/Kaiwei-Lin/huawei_opt_tools.git
cd huawei_opt_tools
```

下面的示例均从本仓库根目录开始。

**生成 ONNX：**

```powershell
cd onnx_omc_generator
python -m pip install -r requirements.txt
python generate_ops.py --op Add --onnx-only --out results_demo
cd ..
```

生成 OMC 需要自行安装匹配的 OMG/DDK，并通过 `--ddk-root` 或 `DDK_PATH` 指定。Linux ELF 版 OMG 在 Linux/WSL 中运行；单独生成 ONNX 可在 Windows 运行。

**使用子图分析工具：**

```powershell
cd op_power_subgraph_extract
python -m pip install -r onnx_graph/requirements.txt
python -m onnx_graph --help
cd ..
```

完整筛选流程、OMC 统计、功耗合并及各绘图入口见该目录的 README。其 Channel 功耗格式与下面的固定列功耗格式不同，按相应工具的说明准备输入。

**平均功耗折线图：**

```powershell
cd power_plot
python -m pip install -r requirements_power.txt
python -X utf8 plot_power.py --input "D:\功耗数据" --output "D:\平均功耗图"
cd ..
```

**各 case 功耗波形：**

```powershell
cd power_waveforms
python -m pip install -r requirements_power.txt
python -X utf8 plot_power_waveforms.py --input "D:\功耗数据" --output "D:\功耗波形"
cd ..
```

## 两个固定列功耗工具的数据约定

- 只读取符合 `编号db.xlsx` 的文件，例如 `0db.xlsx`、`12db.xlsx`；兼容 `a1db.xlsx`、`001_db.xlsx`。其他 Excel 文件直接忽略。
- 第一个 sheet：**A 列为时间戳，D 列为功耗**。输入功耗单位可从表头读取，或使用 `--power-unit W/mW` 指定；波形的数值时间单位不明时再指定 `--input-time-unit s/ms/us/ns`。
- 目录命名为 `convolution_close`、`matmul_close` 等，每个算子分别从 0 编号；兼容旧的 `closed_*` 名称。
- 配套索引包含内源完整类别的 156 个配置，默认来源为内源；逐项对应关系见各工具的 `numbering.csv`。
- 未匹配到模型的文件按直接存放 Excel 的文件夹归为同一种实验类型，不同路径分别分组；缺少的模型 shape 明确标为未知。
- 图中保留 case 与实验类型，并显示输入、常量权重、偏置及输出 shape。例如 `A常量[512,4096] @ B[4096,16] -> Y[512,16]`。

两个绘图目录可单独复制使用。`power_waveforms/plot_power.py` 是波形脚本的配套支持模块，应与该目录下的索引一起保留。

## 来源与验证

导入版本、排除的历史模型包见 [SOURCES.json](SOURCES.json)。两个原仓库按远端 `main` 快照导入，源码与测试保留；绘图工具包含本次对目录命名、第四列功耗、编号文件过滤和 shape 标注的更新。

按目录运行测试：

| 运行目录 | 命令 |
| --- | --- |
| `onnx_omc_generator` | `python -m unittest discover -s . -p "test_*.py" -v` |
| `op_power_subgraph_extract` | `python -m unittest discover -s onnx_graph/tests -v` |
| `power_plot` | `python -m unittest test_plot_power -v` |
| `power_waveforms` | `python -m unittest test_plot_power test_plot_power_waveforms -v` |

仓库包含代码、配置、编号索引和已有验证汇总。DDK、模型二进制、原始 profiling、原始功耗文件及本机结果包由使用者在本地提供。各目录内的历史设备验证记录不代表本次整合重新执行了手机实测。
