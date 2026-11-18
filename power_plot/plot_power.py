"""Offline power plots for numbered OMCs: first worksheet, timestamp A, power D."""
import argparse
from collections import defaultdict
import csv
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
import math
from pathlib import Path
import re
from statistics import fmean
import sys
import textwrap
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None

FILE_PATTERN = re.compile(r"([a-z]+[1-9]\d*|\d+)_?db\.xlsx", re.I)
SOURCES = {"内源": "internal", "internal": "internal", "外源": "external", "external": "external"}
SIDE_LABELS = {"internal": "内源", "external": "外源"}


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("空值或布尔值")
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError("不是数值") from exc
    if not result.is_finite():
        raise ValueError("不是有限数值")
    return result


def timestamp(value):
    if isinstance(value, datetime):
        # Relative differences are independent of the chosen calendar origin.
        base = Decimal(value.toordinal() * 86400 + value.hour * 3600 + value.minute * 60 + value.second)
        return "seconds", base + Decimal(value.microsecond) / 1000000
    if isinstance(value, time):
        return "seconds", Decimal(value.hour * 3600 + value.minute * 60 + value.second) + Decimal(value.microsecond) / 1000000
    if isinstance(value, timedelta):
        return "seconds", Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / 1000000
    try:
        return "numeric", number(value)
    except ValueError:
        if isinstance(value, str):
            for parser in (datetime.fromisoformat, time.fromisoformat):
                try:
                    return timestamp(parser(value.strip()))
                except ValueError:
                    pass
        raise ValueError("无法解析时间戳")


def power_unit(header):
    if re.search(r"(?<![A-Za-z])(?:mW|mw)(?![A-Za-z])|毫瓦", header):
        return "mW"
    if re.search(r"(?<![A-Za-z])(?:W|w)(?![A-Za-z])|(?<!毫)瓦", header):
        return "W"
    return None


def read_power(path, requested_unit="auto"):
    """Stream A/D sample pairs; duplicate times disable only the weighted mean."""
    if load_workbook is None:
        raise ValueError("请先安装 openpyxl：python -m pip install -r requirements_power.txt")
    book = load_workbook(path, read_only=True, data_only=True)
    count = invalid = invalid_time = 0
    mean = m2 = area = 0.0
    low, high = math.inf, -math.inf
    first = previous = kind = None
    power_header = time_header = ""
    time_error = ""
    try:
        sheet = book.worksheets[0]
        sheet_name = sheet.title
        sheet.reset_dimensions()
        for row_number, row in enumerate(sheet.iter_rows(max_col=4, values_only=True), 1):
            a, _, _, c = (list(row) + [None] * 4)[:4]
            if a is None and c is None:
                continue
            try:
                p = float(number(c))
                if not math.isfinite(p):
                    raise ValueError("功耗超出浮点范围")
            except (ValueError, OverflowError):
                if count:
                    invalid += 1
                    time_error = time_error or f"第{row_number}行功耗缺失/无效，加权均值留空"
                else:
                    if isinstance(c, str): power_header = c.strip()
                    if isinstance(a, str): time_header = a.strip()
                continue
            try:
                current_kind, t = timestamp(a)
            except ValueError:
                if not count:
                    # A preamble may contain numbers in D but is not a sample row.
                    continue
                invalid_time += 1
                time_error = time_error or f"第{row_number}行时间戳无效，跳过该行；加权均值留空"
                continue
            count += 1
            delta = p - mean
            mean += delta / count
            m2 += delta * (p - mean)
            low, high = min(low, p), max(high, p)
            if t is not None:
                if first is None:
                    first, kind = t, current_kind
                elif current_kind != kind or t <= previous[0]:
                    time_error = time_error or f"第{row_number}行时间戳重复、倒序或格式混用，加权均值留空"
                elif not time_error:
                    area += (previous[1] + p) * 0.5 * float(t - previous[0])
                previous = (t, p)
        if not count:
            raise ValueError("第一个sheet的第1、4列没有有效采样数据")
        detected = power_unit(power_header)
        unit = detected if requested_unit == "auto" else requested_unit
        if unit is None:
            raise ValueError(f"第4列表头{power_header!r}没有功耗单位，请加 --power-unit W 或 --power-unit mW")
        scale = 1000 if unit == "W" else 1
        span = float(previous[0] - first) if previous and first is not None else None
        weighted = area / span * scale if not time_error and count > 1 and span and span > 0 else None
        warnings = [time_error] if time_error else []
        if invalid:
            warnings.append(f"跳过{invalid}行无效功耗；未将其填为0")
        if requested_unit != "auto" and detected and requested_unit != detected:
            warnings.append(f"手工单位{requested_unit}覆盖表头{detected}")
        if count == 1:
            warnings.append("只有一个有效采样，加权均值留空")
        if kind == "seconds":
            time_unit = "s"
        else:
            match = re.search(r"(?<![A-Za-z])(us|µs|μs|ms|ns|s)(?![A-Za-z])", time_header)
            time_unit = match[1] if match else "原始数值时间单位"
        return dict(sheet=sheet_name, samples=count, invalid_power_rows=invalid, invalid_time_rows=invalid_time, input_power_unit=unit,
            sample_mean_mw=mean*scale, time_mean_mw=weighted, sample_std_mw=math.sqrt(max(0,m2/count))*scale,
            min_mw=low*scale, max_mw=high*scale, time_span=span, time_unit=time_unit, warning="；".join(warnings))
    finally:
        book.close()


def source_for(path, override="auto", default_source=None):
    if override != "auto":
        return override
    found = {SOURCES[p.casefold()] for p in Path(path).parts if p.casefold() in SOURCES}
    if not found and default_source in {"internal", "external"}:
        return default_source
    if len(found) != 1:
        raise ValueError("路径需包含内源/外源或internal/external文件夹；单侧数据可用 --source internal 或 external")
    return found.pop()


def matching_entries(path, index):
    entries = {m['entry']:m['op'] for m in index.values() if m.get('entry') and not m.get('folder_group')}
    parts = {p.casefold() for p in path.parts}
    found = set()
    for entry,op in entries.items():
        name = entry.casefold()
        aliases = {name,op.casefold()}
        if name.startswith('closed_'):aliases.add(name[7:]+'_close')
        if name.endswith('_close'):aliases.add('closed_'+name[:-6])
        if aliases & parts:found.add(entry)
    return found


def resolve_model(path, index):
    """Numeric IDs are local to an operator entry, never global across folders."""
    match = FILE_PATTERN.fullmatch(path.name)
    if match is None:
        raise ValueError("文件名应为编号db.xlsx")
    file_number = match[1].lower()
    found = matching_entries(path,index)
    if len(found) > 1:
        raise ValueError("路径包含多个算子入口，无法确定数字编号所属类别")
    if not file_number.isdigit():
        if file_number not in index:
            raise ValueError(f"原编号{file_number}不在配套索引中")
        if found and index[file_number].get('entry') not in found:
            raise ValueError("算子入口与原编号不一致")
        operators={m['op'].casefold() for m in index.values() if not m.get('folder_group')}
        op_dirs={p.casefold() for p in path.parts} & operators
        if op_dirs and op_dirs!={index[file_number]['op'].casefold()}:
            raise ValueError("算子目录与原编号不一致")
        return file_number
    if not found:
        raise ValueError("纯数字编号需要所属算子文件夹，例如convolution_close/0db.xlsx；不同算子的0号不能混在一起")
    entry = found.pop()
    file_number = str(int(file_number))
    matches = [m['number'] for m in index.values() if not m.get('folder_group')
               and m.get('entry')==entry and m.get('file_number')==file_number]
    if len(matches) != 1:
        raise ValueError(f"{entry}中编号{file_number}不在配套索引内，或映射重复")
    return matches[0]


def power_files(root):
    def order(path):
        return tuple(int(p) if p.isdigit() else p.casefold() for p in re.split(r'(\d+)',path.as_posix()))
    return sorted((p for p in root.rglob('*') if p.is_file() and FILE_PATTERN.fullmatch(p.name)),key=order)


def resolve_case(path, index, root):
    """Use model metadata when matched; otherwise keep the containing folder as a group."""
    match = FILE_PATTERN.fullmatch(path.name)
    if match is None:raise ValueError('文件名应为编号db.xlsx')
    found = matching_entries(path,index)
    if len(found)>1:raise ValueError('路径包含多个算子入口，无法确定所属类别')
    has_entries = any(m.get('entry') and not m.get('folder_group') for m in index.values())
    if found or not has_entries:
        try:return resolve_model(path,index)
        except ValueError:pass
    folder = path.parent.relative_to(root).as_posix()
    label = root.name if folder=='.' else folder
    case = match[1].lower()
    if case.isdigit():case=str(int(case))
    group = 'folder:'+folder
    key = group+':'+case.casefold()
    if key not in index:
        position = 1+sum(m.get('group')==group for m in index.values())
        slug = re.sub(r'[^\w-]+','_',path.parent.name)[:40] or 'data'
        digest = hashlib.sha256(folder.encode('utf-8')).hexdigest()[:10]
        meta = dict(number=key,original_number='',file_number=case,entry=label,op=label,
            group=group,experiment=label,shape='',axis='case',value=position,work='',flops=None,
            movement_bytes=None,selection='按文件夹归类',folder_group=True,
            sequence=max((m.get('sequence',0) for m in index.values()),default=0)+1,
            plot_stem=f'folder_{slug}_{digest}')
        for side in SIDE_LABELS:
            meta.update({side+'_core_us':None,side+'_model_us':None,side+'_transdata':None,
                         side+'_numeric_failed':False,side+'_numeric_status':'未关联模型'})
        index[key]=meta
    return key


def identity(meta):
    return [meta.get('entry',meta['op']),meta.get('file_number',meta['number']),meta.get('original_number',meta['number'])]


def shape_formula(meta):
    """Use recorded tensor shapes; function notation keeps unrecorded attributes implicit."""
    tensors=[line.strip().replace(', ',',') for line in meta.get('shape','').splitlines() if line.strip()]
    if not tensors:return '运算与shape未知（未关联模型）'
    outputs=[t for t in tensors if t.startswith('Y[')]
    inputs=[t for t in tensors if t not in outputs]
    op=meta['op']
    if len(inputs)==2 and op in ('MatMul','BatchMatMul','Add','Mul'):
        symbol={'MatMul':'@','BatchMatMul':'@','Add':'+','Mul':'*'}[op]
        expression=f'{inputs[0]} {symbol} {inputs[1]}'
    else:
        operation={'Convolution':'Conv','GemmD':'Gemm','GemmV3':'Gemm','CastT':'Cast'}.get(op,op)
        if op=='Activation':
            activation=meta.get('experiment','').split('：')[0]
            if activation in ('Sigmoid','Tanh','Relu','LeakyRelu','Elu','HardSigmoid'):
                operation=activation
        expression=f"{operation}({', '.join(inputs)})"
    return expression+' -> '+(', '.join(outputs) if outputs else '输出shape未知')


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def plot_groups(summary, index, output, x_axis, average):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    fonts = {f.name for f in font_manager.fontManager.ttflist}
    font = next((f for f in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC") if f in fonts), "DejaVu Sans")
    chinese = font != "DejaVu Sans"
    groups = defaultdict(list)
    for row in summary:
        groups[index[row["number"]]["group"]].append(row)
    figures = output / "figures"
    figures.mkdir()
    pages, notes = [], []
    for group, rows in sorted(groups.items(), key=lambda item:min(index[r["number"]]["sequence"] for r in item[1])):
        meta = index[rows[0]["number"]]
        key = {"scan":"value", "flops":"flops", "bytes":"movement_bytes"}[x_axis]
        unique_x = {index[r["number"]].get(key) for r in rows}
        if key != "value" and (None in unique_x or (len(unique_x)==1 and len({r['number'] for r in rows})>1)):
            notes.append(f"{group}: {x_axis}缺失或在本类为常量，本图改用{meta['axis']}扫描轴")
            key = "value"
        xlabel = {"value":meta["axis"],"flops":"FLOPs","movement_bytes":"Logical data movement (bytes)"}[key]
        case_models=sorted({r['number'] for r in rows},key=lambda m:index[m]['sequence'])
        formulas=[]
        for model in case_models:
            _,case,original=identity(index[model])
            label=f'case {case}'+(f' ({original})' if original else '')
            formula=textwrap.fill(shape_formula(index[model]),width=100,break_long_words=False,break_on_hyphens=False)
            formulas.append(f'{label}: {formula}')
        if meta.get('folder_group'):formulas=[shape_formula(meta)]
        formula_text='\n\n'.join(formulas)
        formula_height=.35+.21*(formula_text.count('\n')+1)
        with plt.rc_context({"font.sans-serif":[font],"axes.unicode_minus":False,"svg.fonttype":"none"}):
            fig, (ax,formula_ax) = plt.subplots(2,1,figsize=(10,5+formula_height),
                gridspec_kw={'height_ratios':[5,formula_height]},layout="constrained")
            for side,color,marker in [("external","#2876B7","o"),("internal","#D26A25","^")]:
                points = sorted((r for r in rows if r["source"]==side),key=lambda r:(index[r["number"]][key],index[r["number"]]["sequence"]))
                if not points: continue
                # A numeric failure is historical OMC validation, not a power-file error.
                for diagnostic in (False,True):
                    part=[r for r in points if r['diagnostic']==diagnostic]
                    if not part:continue
                    label=(SIDE_LABELS[side] if chinese else side)+(('（历史数值失败）' if chinese else ' (numeric fail)') if diagnostic else '')
                    xs=[index[r['number']][key] for r in part]; ys=[r['mean_mw'] for r in part]
                    ax.errorbar(xs,ys,yerr=[[r['mean_mw']-r['min_run_mw'] for r in part],
                        [r['max_run_mw']-r['mean_mw'] for r in part]],color=color,
                        marker='D' if diagnostic else marker,markerfacecolor='none' if diagnostic else color,
                        linestyle='--' if diagnostic else '-',capsize=3,linewidth=1.6,label=label)
                    for x,y,r in zip(xs,ys,part):
                        ax.annotate(index[r['number']].get('file_number',r['number']),(x,y),xytext=(3,6 if side=='external' else -13),textcoords='offset points',fontsize=8)
            if meta.get('folder_group'):
                title=f"{meta['experiment']} · "+('按文件夹归类' if chinese else 'Folder group')
                case_models=sorted({r['number'] for r in rows},key=lambda m:index[m]['value'])
                ax.set_xticks([index[m]['value'] for m in case_models],
                    [index[m]['file_number'] for m in case_models],rotation=30,ha='right')
            else:
                title=f"{group} · {meta['op']} · "+(meta['experiment'] if chinese else meta['axis']+' sweep')
            ax.set_title(title,wrap=True)
            ax.set_xlabel(xlabel)
            ax.set_ylabel(('平均功耗' if chinese else 'Mean power')+f" (mW) · {'sample mean' if average=='sample' else 'time-weighted'}")
            ax.grid(alpha=.22);ax.spines[['top','right']].set_visible(False)
            ax.legend(frameon=False)
            ax.text(0,-.18,('仅连接已有测量点；误差棒为多轮均值范围' if chinese else 'Measured points only; bars: range of run means'),
                    transform=ax.transAxes,fontsize=9,color='#555555')
            formula_ax.axis('off')
            formula_ax.text(0,1,formula_text,transform=formula_ax.transAxes,va='top',fontsize=10,linespacing=1.4)
            stem=meta.get('plot_stem',f"{group}_{meta['op']}")
            for ext in ('png','svg'):fig.savefig(figures/f'{stem}.{ext}',dpi=180)
            plt.close(fig)
        pages.append(f'<section><h2>{html.escape(title)}</h2><img src="figures/{stem}.png" alt="{html.escape(title)}"></section>')
    page='<!doctype html><meta charset="utf-8"><title>功耗曲线</title><style>body{font-family:Arial,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px}img{width:100%}section{margin:30px 0}</style>'
    page+='<h1>功耗测量汇总</h1><p>读取首个sheet的A/D列。功耗统一为mW；每个曲线点为各文件平均功耗的算术平均。'
    page+='全文件统计，未扣基线、未自动裁剪启动或空闲区间。数值校验标记与时延来自配套OMC的历史记录。</p>'
    page+='<p>图中标记为该算子入口内的文件编号；配置汇总同时保留“入口名称、编号、原编号”。不同入口的同号文件分别统计。</p>'
    page+='<p>未关联模型的文件按所在文件夹归为同一实验类型，横轴为case顺序；shape、计算量和历史时延留空。</p>'
    page+='<p>图内列出各case的运算与完整张量shape：@为矩阵乘法，*为逐元素乘法，箭头指向输出。标注“常量”的项为权重、偏置等固定参数，其他输入项为动态输入。Gemm、卷积等使用函数式写法，不根据shape猜测转置或填充等属性。</p>'
    page+='<p><a href="power_summary.csv">配置汇总</a> · <a href="power_runs.csv">逐文件结果</a> · <a href="coverage.csv">测量覆盖</a> · <a href="errors.csv">读取错误</a></p>'
    page+=''.join('<p>'+html.escape(n)+'</p>' for n in notes)+''.join(pages)
    (output/'index.html').write_text(page,encoding='utf-8')
    return notes


def run(args):
    source_root=args.input.resolve()
    if not source_root.is_dir():raise ValueError(f"输入目录不存在：{source_root}")
    index_data=json.loads(args.index.read_text(encoding='utf-8-sig'))
    index={r['number']:r for r in index_data['models']}
    if len(index)!=len(index_data['models']):raise ValueError('编号索引存在重复编号')
    files=power_files(source_root)
    if not files:raise ValueError('没有找到编号db.xlsx功耗文件')
    output=args.output.resolve() if args.output else source_root/f"power_plots_{datetime.now():%Y%m%d_%H%M%S}"
    if output.exists() and any(output.iterdir()):raise ValueError('输出目录非空，请选择新目录，避免混入旧图')
    output.mkdir(parents=True,exist_ok=True)
    runs,errors=[],[]
    for path in files:
        try:
            model=resolve_case(path,index,source_root)
            side=source_for(path,args.source,index_data.get('default_source'))
            stats=read_power(path,args.power_unit)
            value=stats['sample_mean_mw' if args.average=='sample' else 'time_mean_mw']
            if value is None:raise ValueError('本文件不能计算时间加权均值；使用 --average sample 或修正时间戳/缺失功耗')
            runs.append(dict(number=model,source=side,file=str(path),mean_mw=value,**stats))
        except (ValueError,OSError,KeyError,IndexError,BadZipFile,ParseError) as error:
            errors.append([str(path),str(error)])
    grouped=defaultdict(list)
    for row in runs:grouped[(row['number'],row['source'])].append(row)
    summary=[]
    for (model,side),members in sorted(grouped.items(),key=lambda item:(index[item[0][0]]['sequence'],item[0][1])):
        means=[m['mean_mw'] for m in members]
        summary.append(dict(number=model,source=side,runs=len(means),mean_mw=fmean(means),min_run_mw=min(means),
            max_run_mw=max(means),diagnostic=index[model][side+'_numeric_failed']))
    write_csv(output/'power_runs.csv',['入口名称','编号','原编号','DDK来源','文件','sheet','有效采样数','无效功耗行数','无效时间行数','输入功耗单位',
        '算术平均功耗(mW)','时间加权平均功耗(mW)','本次绘图功耗(mW)','采样标准差(mW)','最小功耗(mW)','最大功耗(mW)',
        '时间跨度','时间单位','备注'],[[*identity(index[r['number']]),SIDE_LABELS[r['source']],r['file'],r['sheet'],r['samples'],r['invalid_power_rows'],r['invalid_time_rows'],
        r['input_power_unit'],r['sample_mean_mw'],r['time_mean_mw'],r['mean_mw'],r['sample_std_mw'],r['min_mw'],r['max_mw'],r['time_span'],r['time_unit'],r['warning']] for r in runs])
    write_csv(output/'power_summary.csv',['入口名称','编号','原编号','DDK来源','算子类型','实验类别','shape','扫描轴','扫描值','计算量','逻辑搬运量(bytes)',
        '测量文件数','平均功耗(mW)','轮均值最小(mW)','轮均值最大(mW)','历史核心时延(us)','历史模型时延(us)','历史TransData数','历史数值校验'],
        [[*identity(index[r['number']]),SIDE_LABELS[r['source']],index[r['number']]['op'],index[r['number']]['experiment'],index[r['number']]['shape'],
          index[r['number']]['axis'],index[r['number']]['value'],index[r['number']]['work'],index[r['number']]['movement_bytes'],r['runs'],
          r['mean_mw'],r['min_run_mw'],r['max_run_mw'],index[r['number']][r['source']+'_core_us'],index[r['number']][r['source']+'_model_us'],
          index[r['number']][r['source']+'_transdata'],index[r['number']][r['source']+'_numeric_status']] for r in summary])
    write_csv(output/'coverage.csv',['入口名称','编号','原编号','算子类型','实验类别','筛选分组','外源测量文件数','内源测量文件数'],
        [[*identity(r),r['op'],r['experiment'],r['selection'],len(grouped.get((r['number'],'external'),[])),
          len(grouped.get((r['number'],'internal'),[]))] for r in index.values()])
    write_csv(output/'errors.csv',['文件','错误'],errors)
    if summary:
        plot_notes=plot_groups(summary,index,output,args.x_axis,args.average)
    else:plot_notes=[]
    result=dict(input=str(source_root),output=str(output),files_found=len(files),valid_files=len(runs),failed_files=len(errors),
        model_source_points=len(summary),average=args.average,power_unit_override=args.power_unit,x_axis=args.x_axis,
        source_override=args.source,index_default_source=index_data.get('default_source'),index_batch=index_data.get('batch'),
        index=str(args.index),plot_notes=plot_notes,
        folder_cases=[dict(entry=identity(index[r['number']])[0],case=identity(index[r['number']])[1],source=r['source'])
                      for r in summary if index[r['number']].get('folder_group')])
    (output/'run_info.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    for path,error in errors[:10]:print(f'ERROR: {path}: {error}',file=sys.stderr)
    return 2 if errors else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True,help='递归查找编号db.xlsx；未知文件夹按文件夹分组')
    parser.add_argument('--output',type=Path,help='新建/空输出目录；默认输入目录下带时间戳的新目录')
    parser.add_argument('--index',type=Path,default=Path(__file__).with_name('power_model_index.json'))
    parser.add_argument('--source',choices=['auto','internal','external'],default='auto',help='默认从目录名识别；无标记时使用配套索引默认来源')
    parser.add_argument('--power-unit',choices=['auto','W','mW'],default='auto',help='第4列原始单位，默认识别表头')
    parser.add_argument('--average',choices=['sample','time'],default='sample',help='默认算术均值；time为梯形积分/时间跨度')
    parser.add_argument('--x-axis',choices=['scan','flops','bytes'],default='scan',help='默认使用各实验类别原来的扫描维度')
    args=parser.parse_args()
    try:return run(args)
    except (ValueError,OSError,ImportError,json.JSONDecodeError) as error:
        print(f'ERROR: {error}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
