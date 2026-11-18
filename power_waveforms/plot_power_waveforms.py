"""One canvas per operator/source; each case shows power D/Y/AB/AE/AN."""
import argparse
from array import array
from collections import defaultdict
from datetime import datetime
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

import numpy as np
import plot_power as common

TIME_SCALE = {'s':1.0,'ms':1e-3,'us':1e-6,'ns':1e-9}


def time_unit_from_header(header):
    for name,unit in [('毫秒','ms'),('微秒','us'),('纳秒','ns'),('秒','s')]:
        if name in header:return unit
    match=re.search(r'(?<![A-Za-z])(us|µs|μs|ms|ns|s)(?![A-Za-z])',header)
    return match[1].replace('µ','u').replace('μ','u') if match else None


def read_waveforms(path, power_unit='auto', input_time_unit='auto', channels=None):
    """Read all rails once, preserving a common time origin and per-rail gaps."""
    if common.load_workbook is None:
        raise ValueError('请先安装依赖：python -m pip install -r requirements_power.txt')
    states={c:dict(powers=array('d'),header='',valid=0,invalid=0,mean=0.0,unit=None)
            for c in (channels or common.POWER_CHANNELS)}
    max_col=max(common.POWER_CHANNELS[c]['index'] for c in states)
    book=common.load_workbook(path,read_only=True,data_only=True)
    times=array('d')
    first=previous=kind=None
    time_header=''
    try:
        sheet=book.worksheets[0]
        sheet.reset_dimensions()
        for row_number,row in enumerate(sheet.iter_rows(max_col=max_col,values_only=True),1):
            row=(list(row)+[None]*max_col)[:max_col]
            a=row[0]
            values={c:row[common.POWER_CHANNELS[c]['index']-1] for c in states}
            if a is None and all(v is None for v in values.values()):continue
            try:
                current_kind,t=common.timestamp(a)
            except ValueError as exc:
                if first is None:
                    if isinstance(a,str):time_header=a.strip()
                    for column,value in values.items():
                        if isinstance(value,str):states[column]['header']=value.strip()
                    continue
                raise ValueError(f'第{row_number}行时间戳无效，无法绘制连续时间波形') from exc
            if first is None:
                first=previous=t;kind=current_kind
                for state in states.values():
                    state['unit']=common.power_unit(state['header']) if power_unit=='auto' else power_unit
                actual_time_unit=('s' if kind=='seconds' else
                    time_unit_from_header(time_header) if input_time_unit=='auto' else input_time_unit)
                if actual_time_unit is None:
                    message='A列表头没有时间单位，请指定 --input-time-unit us/ms/s/ns'
                    if any(s['unit'] is None for s in states.values()):
                        message='功耗列表头没有单位，请指定 --power-unit W 或 mW；'+message
                    raise ValueError(message)
                time_scale=TIME_SCALE[actual_time_unit]
            elif current_kind!=kind or t<=previous:
                raise ValueError(f'第{row_number}行时间戳重复、倒序或格式混用；波形要求严格递增')
            previous=t
            elapsed=float(t-first)*time_scale
            if not math.isfinite(elapsed):raise ValueError('时间跨度超出范围')
            times.append(elapsed)
            for column,state in states.items():
                try:
                    p=float(common.number(values[column]))*(1000.0 if state['unit']=='W' else 1.0)
                    if not math.isfinite(p):raise ValueError('功耗超出范围')
                except (ValueError,OverflowError):
                    p=math.nan;state['invalid']+=1
                else:
                    state['valid']+=1;state['mean']+=(p-state['mean'])/state['valid']
                state['powers'].append(p)
        results,errors={},{}
        x=np.asarray(times)
        for column,state in states.items():
            if not state['valid']:
                errors[column]=f'{column}列没有有效功耗采样（列缺失或无有效功耗）'
                continue
            if state['unit'] is None:
                errors[column]=f'{column}列表头没有功耗单位，请指定 --power-unit W 或 mW'
                continue
            y=np.asarray(state['powers'])
            stats=dict(sheet=sheet.title,samples=len(times),valid_samples=state['valid'],invalid_power_rows=state['invalid'],
                duration_s=times[-1],input_time_unit=actual_time_unit,input_power_unit=state['unit'],
                mean_mw=state['mean'],min_mw=float(np.nanmin(y)),max_mw=float(np.nanmax(y)))
            results[column]=(x,y,stats)
        return results,errors
    finally:book.close()


def read_waveform(path, power_unit='auto', input_time_unit='auto', column='D'):
    results,errors=read_waveforms(path,power_unit,input_time_unit,[column])
    if errors:raise ValueError(errors[column])
    return results[column]


def peak_points(x,y,max_points):
    """Keep each bin's endpoints/extrema and all missing-data boundaries for display."""
    if max_points==0 or len(x)<=max_points:return x,y
    bins=max(1,(max_points-2)//4)
    edges=np.linspace(0,len(x),bins+1,dtype=int)
    keep=[0,len(x)-1]
    for start,stop in zip(edges[:-1],edges[1:]):
        keep.extend((start,stop-1))
        finite=np.flatnonzero(np.isfinite(y[start:stop]))+start
        if len(finite):
            keep.extend((int(finite[np.argmin(y[finite])]),int(finite[np.argmax(y[finite])])))
    # Gap boundaries outrank the nominal point limit: do not draw across missing samples.
    boundaries=np.flatnonzero(np.diff(np.isfinite(y)))
    keep=np.unique(np.concatenate((np.asarray(keep),boundaries,boundaries+1)))
    return x[keep],y[keep]


def font_family(font_file):
    from matplotlib import font_manager
    if font_file:
        font_manager.fontManager.addfont(str(font_file))
        return font_manager.FontProperties(fname=str(font_file)).get_name()
    installed={f.name for f in font_manager.fontManager.ttflist}
    for name in ['Microsoft YaHei','SimHei','Noto Sans CJK SC','Source Han Sans SC','WenQuanYi Micro Hei']:
        if name in installed:return name
    raise ValueError('标注实验类型需要中文字体；请安装中文字体或用 --font-file 指定本地字体文件')


def draw_operator(op,side,cases,index,output,args,errors,records):
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_rgba
    from matplotlib.lines import Line2D
    models=sorted(cases,key=lambda m:index[m]['sequence'])
    cols=min(args.cols,len(models));nrows=math.ceil(len(models)/cols)
    formulas={m:textwrap.fill(common.shape_formula(index[m]),width=42,
              break_long_words=False,break_on_hyphens=False) for m in models}
    extra_lines=max(formula.count('\n')+1 for formula in formulas.values())
    max_repeats=max(len(paths) for paths in cases.values())
    panel_height=3.65+.19*extra_lines+.16*(len(args.channels)+max_repeats+2)
    fig,axes=plt.subplots(nrows,cols,figsize=(5.0*cols,panel_height*nrows+0.7),squeeze=False,
                          sharey=not args.independent_y,layout='constrained')
    groups=list(dict.fromkeys(index[m]['group'] for m in models))
    colors={g:plt.get_cmap('tab10')(i%10) for i,g in enumerate(groups)}
    global_min,global_max=math.inf,-math.inf
    case_stats=[]
    for ax,model in zip(axes.flat,models):
        meta=index[model];entry,case,original=common.identity(meta)
        color=colors[meta['group']]
        paths=sorted(cases[model])
        successes=defaultdict(list);valid_repeats=set();styles={}
        for repeat,path in enumerate(paths,1):
            print(f'读取 {entry}/{case} · {repeat}/{len(paths)} · {path.name}',flush=True)
            try:
                series,channel_errors=read_waveforms(path,args.power_unit,args.input_time_unit,args.channels)
                errors.extend([str(path),common.POWER_CHANNELS[c]['name'],c,error] for c,error in channel_errors.items())
                style=['-','--',':','-.'][repeat-1] if repeat<=4 else (0,(repeat,1,1,1))
                styles[repeat]=style
                for column,(x,y,stats) in series.items():
                    channel=common.POWER_CHANNELS[column]
                    dx,dy=peak_points(x,y,args.max_points)
                    display_x=dx/TIME_SCALE[args.time_unit]
                    ax.plot(display_x,dy,color=channel['color'],linestyle=style,linewidth=.85,
                            marker='o' if stats['valid_samples']==1 else None,
                            label=f"{channel['name']} ({column}) · 轮次{repeat}")
                    successes[column].append(stats);valid_repeats.add(repeat)
                    global_min=min(global_min,stats['min_mw']);global_max=max(global_max,stats['max_mw'])
                    records.append([entry,case,original,common.SIDE_LABELS[side],channel['name'],column,meta['experiment'],meta['shape'],repeat,str(path),
                        stats['sheet'],stats['samples'],stats['valid_samples'],stats['invalid_power_rows'],stats['duration_s'],
                        stats['mean_mw'],stats['min_mw'],stats['max_mw'],len(dx),stats['input_time_unit'],stats['input_power_unit']])
            except (ValueError,OSError,KeyError,IndexError,BadZipFile,ParseError) as exc:
                errors.append([str(path),'','',str(exc)])
        diagnostic=meta[side+'_numeric_failed']
        label=f'case {case}'+(f' · {original}' if original else '')+(' · 历史数值失败' if diagnostic else '')
        if meta.get('folder_group'):
            detail='\n'.join(textwrap.wrap(meta['experiment'],width=26))+'\n按文件夹归类'
        else:
            detail=f"{meta['group']} · {meta['experiment']}\n{meta['axis']}={meta['value']}"
        ax.set_title(f"{label}\n{detail}\n{formulas[model]}",
                     loc='left',fontsize=9.5,color=color,pad=9)
        ax.set_xlabel(f'相对时间 ({args.time_unit})',fontsize=9)
        ax.set_ylabel('功耗 (mW)',fontsize=9)
        ax.tick_params(labelsize=8)
        ax.grid(alpha=.2,linewidth=.5)
        ax.spines[['top','right']].set_visible(False)
        ax.set_facecolor(to_rgba(color,.035))
        if successes:
            text=f"有效{len(valid_repeats)}/{len(paths)}轮 · {len(successes)}/{len(set(args.channels))}路功耗"
            ax.text(.02,.97,text,transform=ax.transAxes,va='top',fontsize=8,
                    bbox=dict(facecolor='white',edgecolor='none',alpha=.8,pad=2))
            handles=[Line2D([0],[0],color=common.POWER_CHANNELS[c]['color'],linewidth=1.5,
                label=f"{common.POWER_CHANNELS[c]['name']} ({c}) · 均值{fmean(s['mean_mw'] for s in stats):.2f} mW")
                for c,stats in successes.items()]
            if len(paths)>1:
                handles.extend(Line2D([0],[0],color='#555555',linestyle=styles[r],label=f'轮次{r}') for r in sorted(valid_repeats))
            ax.legend(handles=handles,loc='upper left',bbox_to_anchor=(0,-.23),fontsize=7,frameon=False,borderaxespad=0)
            if max(s['duration_s'] for stats in successes.values() for s in stats)>0:ax.set_xlim(left=0)
        else:
            ax.text(.5,.5,'无法读取波形\n详见 errors.csv',ha='center',va='center',transform=ax.transAxes,color='#9C233C')
        case_stats.append(dict(entry=entry,case=case,original=original,experiment=meta['experiment'],
            input_files=len(paths),valid_files=len(valid_repeats),valid_channels={c:len(s) for c,s in successes.items()},
            historical_numeric_failed=diagnostic,
            formula=common.shape_formula(meta)))
    for ax in list(axes.flat)[len(models):]:ax.set_visible(False)
    if not args.independent_y and math.isfinite(global_min):
        margin=max((global_max-global_min)*.07,abs(global_max)*.01,.01)
        axes.flat[0].set_ylim(global_min-margin,global_max+margin)
    y_note='各case独立功耗刻度' if args.independent_y else '同算子统一功耗刻度'
    subtitle=('\n' if cols==1 else '；').join([
        '各文件起始时间归零',y_note,'颜色区分功耗通道，线型区分轮次'])
    title='\n'.join(textwrap.wrap(f'{op} · {common.SIDE_LABELS[side]} · {len(models)} cases',width=28*cols))
    fig.suptitle(f'{title}\n{subtitle}',fontsize=14)
    meta=index[models[0]]
    stem=f'{meta.get("plot_stem",meta.get("entry",op))}_{side}_waveforms'
    for ext in ('png','svg'):fig.savefig(output/'figures'/f'{stem}.{ext}',dpi=160,bbox_inches='tight',pad_inches=.12)
    plt.close(fig)
    return dict(op=op,source=side,cases=case_stats,png=f'figures/{stem}.png',svg=f'figures/{stem}.svg')


def run(args):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    source_root=args.input.resolve()
    if not source_root.is_dir():raise ValueError(f'输入目录不存在：{args.input}')
    if not 1<=args.cols<=8:raise ValueError('--cols应为1至8')
    if args.max_points!=0 and args.max_points<8:raise ValueError('--max-points应至少为8，或用0显示全部采样')
    data=json.loads(args.index.read_text(encoding='utf-8-sig'))
    index={r['number']:r for r in data['models']}
    if len(index)!=len(data['models']):raise ValueError('索引存在重复的原模型编号')
    files=common.power_files(source_root)
    if not files:raise ValueError('没有找到编号db.xlsx功耗文件')
    font=font_family(args.font_file)
    output=args.output or args.input/f'power_waveforms_{datetime.now():%Y%m%d_%H%M%S}'
    if output.exists() and any(output.iterdir()):raise ValueError('输出目录非空，请使用新目录')
    (output/'figures').mkdir(parents=True,exist_ok=True)
    batches=defaultdict(lambda:defaultdict(list));errors=[]
    for path in files:
        try:
            model=common.resolve_case(path,index,source_root)
            side=common.source_for(path,args.source,data.get('default_source'))
            meta=index[model]
            batches[(meta.get('plot_stem',meta['op']),side)][model].append(path)
        except ValueError as exc:errors.append([str(path),'','',str(exc)])
    records=[];canvases=[]
    with plt.rc_context({'font.sans-serif':[font],'axes.unicode_minus':False,'svg.fonttype':'none',
                         'path.simplify':False,'agg.path.chunksize':10000}):
        for (_,side),cases in sorted(batches.items(),key=lambda item:min(index[m]['sequence'] for m in item[1])):
            op=index[next(iter(cases))]['op']
            canvases.append(draw_operator(op,side,cases,index,output,args,errors,records))
    common.write_csv(output/'waveform_summary.csv',['入口名称','编号','原编号','DDK来源','功耗通道','功耗列','实验类型','完整shape','轮次','文件',
        'sheet','时间采样数','有效功耗数','无效功耗行数','记录时长(s)','算术平均功耗(mW)','最小功耗(mW)','最大功耗(mW)',
        '显示点数','原始时间单位','原始功耗单位'],records)
    common.write_csv(output/'errors.csv',['文件','功耗通道','功耗列','错误'],errors)
    page='<!doctype html><meta charset="utf-8"><title>各算子功耗波形</title><style>body{font-family:Arial,sans-serif;max-width:1800px;margin:24px auto;padding:0 16px}img{width:100%}section{margin:32px 0}</style>'
    page+='<h1>各算子功耗波形</h1><p>每个case一个子图；按所属算子入口匹配数字编号。标题标注原编号、实验类型及运算公式，包含输入、权重、偏置和输出shape。'
    page+='重复测量在对应子图中分别显示；时间各自从0开始，不表示不同case的采集发生在同一时刻。'
    page+='缺失功耗保留断线；默认保留分段峰谷减少显示点数，没有做平滑、基线扣除或自动裁剪。</p>'
    page+='<p>未关联模型的文件按所在文件夹归为同一实验类型，并单独输出画布；不推测shape或历史实验参数。</p>'
    page+='<p>读取首个sheet的A列时间戳及所选功耗列（默认D/Y/AB/AE/AN），各路独立统计，不相加。颜色区分通道，线型区分重复轮次；图例标明通道及各轮均值的等权平均。缺失通道记录在错误表，其他通道继续绘制。</p>'
    page+='<p>公式中@为矩阵乘法，*为逐元素乘法，箭头指向输出。标注“常量”的项为权重、偏置等固定参数，其他输入项为动态输入。Gemm、卷积等采用函数式写法，不根据shape猜测转置或填充等属性。</p>'
    page+='<p><a href="waveform_summary.csv">采样与完整shape汇总</a> · <a href="errors.csv">错误记录</a></p>'
    for canvas in canvases:
        title=html.escape(f"{canvas['op']} · {common.SIDE_LABELS[canvas['source']]} · {len(canvas['cases'])} cases")
        page+=f'<section><h2>{title}</h2><p><a href="{canvas["png"]}">打开原尺寸PNG</a> · <a href="{canvas["svg"]}">打开SVG</a></p><img src="{canvas["png"]}" alt="{title}"></section>'
    (output/'index.html').write_text(page,encoding='utf-8')
    result=dict(input=str(args.input.resolve()),output=str(output.resolve()),index=str(args.index.resolve()),
        files_found=len(files),valid_files=len({r[9] for r in records}),failed_files=len({r[0] for r in errors}),
        valid_file_channels=len(records),channel_errors=len(errors),channels={c:common.POWER_CHANNELS[c]['name'] for c in args.channels},
        case_count=sum(len(c['cases']) for c in canvases),
        canvases=canvases,columns=args.cols,display_time_unit=args.time_unit,power_unit_override=args.power_unit,
        input_time_unit_override=args.input_time_unit,max_points=args.max_points,shared_power_scale=not args.independent_y,
        display_reduction='Per-bin endpoints/min/max; gap boundaries retained. Statistics use all valid samples.')
    (output/'run_info.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='canvases'},ensure_ascii=False,indent=2))
    for path,channel,column,error in errors[:10]:print(f'ERROR: {path} [{channel} {column}]: {error}',file=sys.stderr)
    return 2 if errors else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True,help='功耗数据根目录，递归查找编号db.xlsx')
    parser.add_argument('--output',type=Path,help='新建/空输出目录')
    parser.add_argument('--index',type=Path,default=Path(__file__).with_name('power_model_index.json'))
    parser.add_argument('--source',choices=['auto','internal','external'],default='auto')
    parser.add_argument('--power-unit',choices=['auto','W','mW'],default='auto',help='所有所选功耗列的原始单位；默认分别识别各列表头')
    parser.add_argument('--channels',nargs='+',type=str.upper,choices=list(common.POWER_CHANNELS),default=list(common.POWER_CHANNELS),help='所选功耗列，默认D Y AB AE AN；只看NPU可用 --channels D')
    parser.add_argument('--input-time-unit',choices=['auto','s','ms','us','ns'],default='auto',help='A列数值时间戳的原始单位')
    parser.add_argument('--time-unit',choices=['s','ms','us'],default='ms',help='图的时间显示单位')
    parser.add_argument('--cols',type=int,default=4,help='每行子图数，默认4')
    parser.add_argument('--independent-y',action='store_true',help='各case独立功耗刻度；默认同算子统一')
    parser.add_argument('--max-points',type=int,default=4000,help='每条曲线显示点数目标；0表示全部；缺失边界可超过目标')
    parser.add_argument('--font-file',type=Path,help='可选的本地中文字体文件')
    args=parser.parse_args()
    try:return run(args)
    except (ValueError,OSError,ImportError,json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
