"""Small, synthetic checks for power arithmetic and directory identification."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import plot_power as pp


class Sheet:
    title = 'First'
    def __init__(self, rows): self.rows = rows
    def reset_dimensions(self): pass
    def iter_rows(self, **kwargs): return (row[:kwargs.get('max_col',len(row))] for row in self.rows)


class Book:
    def __init__(self, rows): self.worksheets = [Sheet(rows), Sheet([(0,0,0,999999)])]
    def close(self): pass


class PowerTests(unittest.TestCase):
    def read(self, rows, unit='auto'):
        with patch.object(pp, 'load_workbook', return_value=Book(rows)) as loader:
            result=pp.read_power(Path('a1db.xlsx'),unit)
            self.assertTrue(loader.call_args.kwargs['read_only'])
            self.assertTrue(loader.call_args.kwargs['data_only'])
            return result

    def test_first_sheet_fourth_column_and_irregular_time(self):
        r=self.read([('Timestamp[us]','ignored','not power','Power[W]'),
                     (0,999,888,1),(1,999,888,3),(3,999,888,3),(4,999,888,1)])
        self.assertEqual(r['sample_mean_mw'],2000)
        self.assertEqual(r['time_mean_mw'],2500)
        self.assertEqual(r['samples'],4)
        self.assertEqual(r['sheet'],'First')
        self.assertEqual(r['time_unit'],'us')

    def test_milliwatts_and_zero_are_real_values(self):
        r=self.read([('t','ignored','ignored','功耗(mW)'),(10,None,999,0),(20,None,999,2),(30,None,999,4)])
        self.assertEqual(r['sample_mean_mw'],2)
        self.assertEqual(r['min_mw'],0)
        self.assertEqual(r['time_mean_mw'],2)

    def test_unknown_unit_requires_explicit_choice(self):
        rows=[('t','ignored','ignored','功耗'),(0,None,999,2),(1,None,999,4)]
        with self.assertRaisesRegex(ValueError,'--power-unit'):
            self.read(rows)
        self.assertEqual(self.read(rows,'mW')['sample_mean_mw'],3)

    def test_duplicate_time_keeps_sample_mean_not_weighted(self):
        r=self.read([('t',None,None,'W'),(0,None,999,2),(0,None,999,4),(1,None,999,6)])
        self.assertEqual(r['sample_mean_mw'],4000)
        self.assertIsNone(r['time_mean_mw'])
        self.assertIn('重复',r['warning'])

    def test_missing_power_not_zero_or_interpolated(self):
        r=self.read([('t',None,None,'mW'),(0,None,999,2),(1,None,999,None),(2,None,999,4)])
        self.assertEqual(r['sample_mean_mw'],3)
        self.assertEqual(r['invalid_power_rows'],1)
        self.assertIsNone(r['time_mean_mw'])

    def test_datetime_timestamps(self):
        r=self.read([('时间',None,None,'Power[W]'),(datetime(2026,11,17,1,0,0),None,999,1),
                     (datetime(2026,11,17,1,0,2),None,999,3)])
        self.assertEqual(r['time_span'],2)
        self.assertEqual(r['time_unit'],'s')
        self.assertEqual(r['time_mean_mw'],2000)

    def test_numeric_footer_is_not_a_sample(self):
        r=self.read([('t',None,None,'mW'),(0,None,999,1),(1,None,999,3),('平均',None,999,999)])
        self.assertEqual(r['sample_mean_mw'],2)
        self.assertEqual(r['samples'],2)
        self.assertEqual(r['invalid_time_rows'],1)
        self.assertIsNone(r['time_mean_mw'])

    def test_one_sample_is_not_duration(self):
        r=self.read([('t',None,None,'mW'),(5,None,999,8)])
        self.assertEqual(r['sample_mean_mw'],8)
        self.assertIsNone(r['time_mean_mw'])

    def test_large_integer_timestamps_preserve_interval(self):
        t=10**20
        r=self.read([('t',None,None,'mW'),(t,None,999,2),(t+1,None,999,4)])
        self.assertEqual(r['time_span'],1)
        self.assertEqual(r['time_mean_mw'],3)

    def test_source_and_filename(self):
        self.assertEqual(pp.source_for(Path('data/内源/完整类别/Add/a1db.xlsx')),'internal')
        self.assertEqual(pp.source_for(Path('data/external/Add/a1db.xlsx')),'external')
        with self.assertRaises(ValueError):pp.source_for(Path('data/a1db.xlsx'))
        with self.assertRaises(ValueError):pp.source_for(Path('internal/external/a1db.xlsx'))
        self.assertEqual(pp.source_for(Path('data/a1db.xlsx'),'external'),'external')
        self.assertEqual(pp.FILE_PATTERN.fullmatch('AQ1db.xlsx')[1].lower(),'aq1')
        self.assertIsNone(pp.FILE_PATTERN.fullmatch('~$a1db.xlsx'))

    def test_zero_based_filenames_and_internal_default(self):
        self.assertEqual(pp.FILE_PATTERN.fullmatch('0db.xlsx')[1],'0')
        self.assertEqual(pp.FILE_PATTERN.fullmatch('155db.xlsx')[1],'155')
        self.assertEqual(pp.FILE_PATTERN.fullmatch('001_db.xlsx')[1],'001')
        self.assertIsNone(pp.FILE_PATTERN.fullmatch('a0db.xlsx'))
        self.assertIsNone(pp.FILE_PATTERN.fullmatch('~$0db.xlsx'))
        self.assertEqual(pp.source_for(Path('data/Convolution/0db.xlsx'),default_source='internal'),'internal')
        self.assertEqual(pp.source_for(Path('data/外源/0db.xlsx'),default_source='internal'),'external')
        self.assertEqual(pp.source_for(Path('data/0db.xlsx'),'external',default_source='internal'),'external')
        with self.assertRaises(ValueError):
            pp.source_for(Path('internal/external/0db.xlsx'),default_source='internal')

    def test_per_operator_zero_does_not_collide(self):
        index={
            'a1':dict(number='a1',op='Convolution',entry='closed_convolution',file_number='0'),
            'a4':dict(number='a4',op='Convolution',entry='closed_convolution',file_number='1'),
            'g1':dict(number='g1',op='MatMul',entry='closed_matmul',file_number='0'),
        }
        self.assertEqual(pp.resolve_model(Path('data/closed_convolution/0db.xlsx'),index),'a1')
        self.assertEqual(pp.resolve_model(Path('data/closed_matmul/0db.xlsx'),index),'g1')
        self.assertEqual(pp.resolve_model(Path('data/Convolution/001_db.xlsx'),index),'a4')
        self.assertEqual(pp.resolve_model(Path('data/closed_convolution/run02/0db.xlsx'),index),'a1')
        self.assertEqual(pp.resolve_model(Path('data/a1db.xlsx'),index),'a1')
        for path in ['data/0db.xlsx','closed_matmul/1db.xlsx','closed_convolution/closed_matmul/0db.xlsx',
                     'closed_matmul/a1db.xlsx']:
            with self.subTest(path=path),self.assertRaises(ValueError):
                pp.resolve_model(Path(path),index)
        self.assertEqual(pp.identity(index['a1']),['closed_convolution','0','a1'])

    def test_close_suffix_and_legacy_alias(self):
        for entry in ['convolution_close','closed_convolution']:
            index={'a1':dict(number='a1',op='Convolution',entry=entry,file_number='0')}
            for folder in ['convolution_close','closed_convolution','Convolution','CONVOLUTION_CLOSE']:
                with self.subTest(entry=entry,folder=folder):
                    self.assertEqual(pp.resolve_case(Path('data')/folder/'0db.xlsx',index,Path('data')),'a1')

    def test_unknown_folders_do_not_collide_or_borrow_metadata(self):
        root=Path('data')
        index={'a1':dict(number='a1',op='Convolution',entry='convolution_close',file_number='0')}
        keys=[pp.resolve_case(root/p,index,root) for p in
              ['extra/0db.xlsx','other/extra/0db.xlsx','extra/a1db.xlsx','extra/8db.xlsx']]
        self.assertEqual(len(set(keys)),4)
        self.assertEqual(index[keys[0]]['group'],index[keys[2]]['group'])
        self.assertNotEqual(index[keys[0]]['group'],index[keys[1]]['group'])
        self.assertEqual(pp.resolve_case(root/'extra/000_db.xlsx',index,root),keys[0])
        for key in keys:
            self.assertTrue(index[key]['folder_group'])
            self.assertEqual(index[key]['original_number'],'')
            self.assertEqual(index[key]['shape'],'')
            self.assertIsNone(index[key]['flops'])
            self.assertIsNone(index[key]['internal_core_us'])
            self.assertNotIn('/',index[key]['plot_stem'])
        self.assertTrue(index[pp.resolve_case(root/'a1db.xlsx',index,root)]['folder_group'])

    def test_unmatched_number_uses_folder_without_becoming_known_mapping(self):
        root=Path('data')
        index={'a1':dict(number='a1',op='Convolution',entry='convolution_close',file_number='0')}
        path=root/'convolution_close/999db.xlsx'
        key=pp.resolve_case(path,index,root)
        self.assertTrue(index[key]['folder_group'])
        self.assertEqual(pp.resolve_case(path,index,root),key)
        with self.assertRaises(ValueError):pp.resolve_model(path,index)
        self.assertEqual(pp.resolve_case(root/'convolution_close/run02/0db.xlsx',index,root),'a1')

    def test_ambiguous_known_folders_remain_an_error(self):
        index={
            'a1':dict(number='a1',op='Convolution',entry='convolution_close',file_number='0'),
            'g1':dict(number='g1',op='MatMul',entry='matmul_close',file_number='0'),
        }
        with self.assertRaisesRegex(ValueError,'多个算子入口'):
            pp.resolve_case(Path('data/convolution_close/matmul_close/0db.xlsx'),index,Path('data'))

    def test_legacy_letter_index_keeps_operator_validation(self):
        index={'a1':dict(number='a1',op='Convolution'),'g1':dict(number='g1',op='MatMul')}
        self.assertEqual(pp.resolve_case(Path('data/Convolution/a1db.xlsx'),index,Path('data')),'a1')
        wrong=pp.resolve_case(Path('data/MatMul/a1db.xlsx'),index,Path('data'))
        self.assertTrue(index[wrong]['folder_group'])

    def test_excel_discovery_and_natural_case_order(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); folder=root/'extra';folder.mkdir()
            for name in ['10db.xlsx','2db.xlsx','0db.xlsx','AQ1DB.XLSX','测量.XLSX','~$0db.xlsx','notes.csv','2db_backup.xlsx']:
                (folder/name).touch()
            files=pp.power_files(root)
            self.assertEqual([p.name for p in files],['0db.xlsx','2db.xlsx','10db.xlsx','AQ1DB.XLSX'])
            index={}
            models=[pp.resolve_case(p,index,root) for p in files]
            self.assertEqual([index[m]['value'] for m in models],[1,2,3,4])

    def test_non_numbered_workbooks_cannot_become_folder_cases(self):
        root=Path('data');index={}
        for name in ['测量.xlsx','db.xlsx','2db_backup.xlsx','~$0db.xlsx','0db.xlsx.tmp']:
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'编号db.xlsx'):
                pp.resolve_case(root/'unknown'/name,index,root)
        self.assertEqual(index,{})

    def test_matmul_formula_preserves_constant_left_operand(self):
        meta=dict(op='MatMul',shape='A常量[512, 4096]\nB[4096, 16]\nY[512, 16]')
        self.assertEqual(pp.shape_formula(meta),'A常量[512,4096] @ B[4096,16] -> Y[512,16]')

    def test_convolution_formula_includes_weights_bias_and_output(self):
        meta=dict(op='Convolution',shape='X[1, 64, 2, 8192]\nW常量[128, 64, 1, 1]\nB常量[128]\nY[1, 128, 2, 8192]')
        self.assertEqual(pp.shape_formula(meta),
            'Conv(X[1,64,2,8192], W常量[128,64,1,1], B常量[128]) -> Y[1,128,2,8192]')

    def test_gemm_formula_does_not_guess_transpose_or_alpha(self):
        meta=dict(op='GemmD',shape='A[256, 1024]\nB常量[1024, 1024]\nC常量[1024]\nY[256, 1024]')
        result=pp.shape_formula(meta)
        self.assertEqual(result,'Gemm(A[256,1024], B常量[1024,1024], C常量[1024]) -> Y[256,1024]')

    def test_activation_and_unknown_shape_labels(self):
        meta=dict(op='Activation',experiment='Sigmoid：元素数',shape='X[360448]\nY[360448]')
        self.assertEqual(pp.shape_formula(meta),'Sigmoid(X[360448]) -> Y[360448]')
        self.assertIn('shape未知',pp.shape_formula(dict(op='other',shape='')))


if __name__=='__main__':unittest.main()
