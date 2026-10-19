import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prepare_pvf10 import classify


class PVF10PreparationTests(unittest.TestCase):
    def test_folder_number_does_not_change_semantic_label(self):
        stem = "DJI_20230223120000_0001_T_000001.png"
        first = classify(f"PVF-10/PVF_10_112x112/test/01substring open circuit/{stem}")
        second = classify(f"PVF-10/PVF_10_112x112/train/09substring open circuit/{stem}")
        self.assertEqual(first[2], second[2])
        self.assertEqual(first[3], second[3])
        self.assertEqual(first[5], "validation")

    def test_capture_date_and_photo_group(self):
        path = ("PVF-10/PVF_10_112x112/train/10healthy panel/"
                "DJI_20231114113742_0466_T_000003.png")
        published, _, label, group, date, split = classify(path)
        self.assertEqual((published, label, group, date, split),
                         ("train", "healthy panel", "DJI_20231114113742_0466_T",
                          "20231114", "test"))


if __name__ == "__main__":
    unittest.main()
