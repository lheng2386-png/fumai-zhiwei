import csv
import json
import tempfile
import unittest
from pathlib import Path
from agent.core import InputError, Snapshot
from agent.import_snapshot import convert


class ImportTests(unittest.TestCase):
    def test_project_events_csv_round_trip_and_bad_number(self):
        root=Path(__file__).resolve().parents[1]
        document=json.loads((root/'demo-snapshot.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            folder=Path(directory);events=folder/'events.csv';devices=folder/'devices.json'
            devices.write_text(json.dumps(document['devices']),encoding='utf-8')
            rows=document['events']
            fields=list(rows[0])
            with events.open('w',encoding='utf-8-sig',newline='') as file:
                writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(rows)
            metadata={key:document[key] for key in ('site_id','dataset_version','screening_version','timezone','observed_at')}
            result=convert(events,devices,metadata)
            self.assertEqual(result['source'],'measured')
            self.assertEqual(Snapshot(result,'live').events[rows[0]['event_id']]['mean_actual_kw'],9.4)
            rows[0]['mean_actual_kw']='nan'
            with events.open('w',encoding='utf-8',newline='') as file:
                writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(rows)
            with self.assertRaises(InputError):convert(events,devices,metadata)
            assert folder.resolve().is_relative_to(Path(__file__).resolve().parent)


if __name__=='__main__':unittest.main()
