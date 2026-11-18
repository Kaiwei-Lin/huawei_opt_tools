from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import plot_power_waveforms as pw
from test_plot_power import Book, channel_rows


class WaveformTests(unittest.TestCase):
    def read(self, rows, **kwargs):
        with patch.object(pw.common,'load_workbook',return_value=Book(rows)):
            return pw.read_waveform(Path('0db.xlsx'),**kwargs)

    def test_first_sheet_and_columns_units_and_zero_origin(self):
        x,y,s=self.read([('Timestamp[us]','ignored','not power','Power[W]'),
                        (1000000,999,888,1),(1001000,999,888,3),(1004000,999,888,2)])
        np.testing.assert_allclose(x,[0,.001,.004])
        np.testing.assert_array_equal(y,[1000,3000,2000])
        self.assertEqual(s['mean_mw'],2000)
        self.assertEqual(s['valid_samples'],3)

    def test_all_rails_share_time_and_preserve_independent_units(self):
        book=Book(channel_rows())
        with patch.object(pw.common,'load_workbook',return_value=book) as loader, \
             patch.object(book.worksheets[0],'iter_rows',wraps=book.worksheets[0].iter_rows) as scan:
            series,errors=pw.read_waveforms(Path('0db.xlsx'))
        self.assertEqual(errors,{})
        loader.assert_called_once();scan.assert_called_once()
        self.assertEqual(scan.call_args.kwargs['max_col'],40)
        for c,expected in [('D',[1000,3000,5000]),('Y',[10,20,30]),('AB',[100,300,500]),
                           ('AE',[50,75,100]),('AN',[2,4,6])]:
            with self.subTest(column=c):
                x,y,stats=series[c]
                np.testing.assert_allclose(x,[0,.000001,.000004])
                np.testing.assert_allclose(y,expected)
                self.assertIs(x,series['D'][0])
                self.assertAlmostEqual(stats['mean_mw'],sum(expected)/3)

    def test_rail_gaps_keep_common_origin_and_missing_rail_is_isolated(self):
        rows=channel_rows();rows[1][24]=None;rows[2][27]=None
        for row in rows:row[39]=None
        with patch.object(pw.common,'load_workbook',return_value=Book(rows)):
            series,errors=pw.read_waveforms(Path('0db.xlsx'))
        self.assertEqual(set(errors),{'AN'})
        self.assertEqual(set(series),{'D','Y','AB','AE'})
        x,y,stats=series['Y']
        self.assertEqual(x[0],0)
        self.assertTrue(np.isnan(y[0]))
        self.assertEqual(stats['mean_mw'],25)
        self.assertTrue(np.isnan(series['AB'][1][1]))
        np.testing.assert_array_equal(series['D'][1],[1000,3000,5000])

    def test_unknown_unit_affects_only_its_channel_and_subset_is_supported(self):
        rows=channel_rows();rows[0][24]='Vbuck8_1v8'
        with patch.object(pw.common,'load_workbook',return_value=Book(rows)):
            series,errors=pw.read_waveforms(Path('0db.xlsx'),channels=['D','Y'])
        self.assertEqual(set(errors),{'Y'})
        self.assertIn('--power-unit',errors['Y'])
        self.assertEqual(set(series),{'D'})
        with patch.object(pw.common,'load_workbook',return_value=Book(rows)):
            series,errors=pw.read_waveforms(Path('0db.xlsx'),power_unit='mW',channels=['Y'])
        self.assertEqual(errors,{})
        np.testing.assert_array_equal(series['Y'][1],[10,20,30])

    def test_missing_power_stays_a_gap(self):
        x,y,s=self.read([('Time(ms)',None,None,'mW'),(5,None,999,2),(6,None,999,None),(7,None,999,4)])
        np.testing.assert_allclose(x,[0,.001,.002])
        self.assertTrue(np.isnan(y[1]))
        self.assertEqual(s['invalid_power_rows'],1)
        self.assertEqual(s['mean_mw'],3)

    def test_unknown_units_require_parameters(self):
        rows=[('time',None,None,'power'),(0,None,999,1),(2,None,999,2)]
        with self.assertRaisesRegex(ValueError,'power-unit'):self.read(rows)
        with self.assertRaisesRegex(ValueError,'input-time-unit'):self.read(rows,power_unit='mW')
        x,y,_=self.read(rows,power_unit='mW',input_time_unit='s')
        np.testing.assert_array_equal(x,[0,2])

    def test_bad_timestamps_are_not_reordered(self):
        for t in [1,0,'not a time']:
            with self.subTest(t=t),self.assertRaises(ValueError):
                self.read([('Time(s)',None,None,'mW'),(1,None,999,1),(t,None,999,2)])

    def test_datetime_and_large_timestamp_precision(self):
        x,_,_=self.read([('time',None,None,'mW'),(datetime(2026,11,17,1,0,0),None,999,1),
                         (datetime(2026,11,17,1,0,2),None,999,2)])
        np.testing.assert_array_equal(x,[0,2])
        x,_,_=self.read([('time[us]',None,None,'mW'),(10**20,None,999,1),(10**20+1,None,999,2)])
        np.testing.assert_allclose(x,[0,.000001])

    def test_peak_reduction_keeps_peaks_edges_and_gaps(self):
        x=np.arange(10000,dtype=float);y=np.zeros(10000)
        y[1234]=999;y[4567]=-500;y[7000:7010]=np.nan
        a,b=pw.peak_points(x,y,200)
        self.assertEqual(a[0],0);self.assertEqual(a[-1],9999)
        self.assertEqual(np.nanmax(b),999);self.assertEqual(np.nanmin(b),-500)
        self.assertTrue({6999,7000,7009,7010}.issubset(set(a)))
        self.assertTrue(np.all(np.diff(a)>0));self.assertLess(len(a),210)
        np.testing.assert_array_equal(a[~np.isfinite(b)],[7000,7009])

    def test_full_display_and_unit_aliases(self):
        x=np.arange(12);y=np.arange(12,dtype=float)
        a,b=pw.peak_points(x,y,0)
        np.testing.assert_array_equal(a,x);np.testing.assert_array_equal(b,y)
        for title,unit in [('Timestamp[µs]','us'),('时间（毫秒）','ms'),('time(s)','s')]:
            self.assertEqual(pw.time_unit_from_header(title),unit)


if __name__=='__main__':unittest.main()
