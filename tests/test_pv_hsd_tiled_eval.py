import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from evaluate_pv_hsd_tiled import nms, restore_box, touches_internal_edge


class TiledDetectorEvaluationTests(unittest.TestCase):
    def test_restored_boxes_from_overlapping_tiles_merge(self):
        first = restore_box([300, 200, 312, 210], (0, 0))
        second = restore_box([44, 8, 56, 18], (256, 192))
        self.assertEqual(first, second)
        self.assertEqual(nms([first, second], [0.9, 0.8]), [0])

    def test_internal_crop_edge_is_filtered_but_image_edge_is_allowed(self):
        self.assertTrue(touches_internal_edge([0, 10, 8, 20], (256, 0)))
        self.assertFalse(touches_internal_edge([0, 10, 8, 20], (0, 0)))
        self.assertTrue(touches_internal_edge([376, 10, 384, 20], (0, 0)))
        self.assertFalse(touches_internal_edge([376, 10, 384, 20], (256, 0)))


if __name__ == "__main__":
    unittest.main()
