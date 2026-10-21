import sys
from pathlib import Path
import unittest
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from audit_tecnalia_pv import describe

class TecnaliaAuditTests(unittest.TestCase):
    def test_actual_timing_sentinel_and_zero_power_are_not_silently_repaired(self):
        frame=pd.DataFrame({'Fecha':['2024-11-01 00:00','2024-11-01 00:10','2024-11-01 00:10'],
            'Vmpp(V)':[0,10,10],'Impp(A)':[0,2,2],'Pmpp (W)':[0,20,25],
            'Front GPOA (W/m²)':[0,400,400],'Temp. Mod (°C)':[-9999,15,16],'unavailable':[None]*3})
        r=describe(frame)
        self.assertEqual(r['median_positive_step_minutes'],10.)
        self.assertEqual(r['duplicate_timestamps'],1)
        self.assertEqual(r['finite_by_column']['Pmpp (W)'],3)
        self.assertEqual(r['sentinel_minus9999_by_column'],{'Temp. Mod (°C)':1})
        self.assertEqual(r['entirely_missing_columns'],['unavailable'])
        self.assertEqual(r['available_label_columns'],[])
        self.assertEqual(r['power_consistency']['rows_abs_P_minus_VI_above_1W'],1)
        self.assertEqual(frame['Temp. Mod (°C)'].iloc[0],-9999)

if __name__=='__main__':unittest.main()
