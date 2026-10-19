import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prepare_pv_hsd_tiles import TILE_SIZE, TILES, tile_box


class PVHSDTileTests(unittest.TestCase):
    def test_edge_box_fully_covered_and_normalized(self):
        box = (625, 498, 639, 511)
        tile = tile_box(box, 256, 192)
        self.assertIsNotNone(tile)
        self.assertTrue(all(0 <= coordinate <= 1 for coordinate in tile))
        self.assertAlmostEqual(tile[2] * TILE_SIZE[0], 14)
        self.assertAlmostEqual(tile[3] * TILE_SIZE[1], 13)

    def test_overlap_gives_a_complete_tile_for_small_boxes(self):
        for x in range(0, 640 - 127, 7):
            for y in range(0, 512 - 108, 11):
                box = (x, y, x + 127, y + 108)
                self.assertTrue(any(left <= x and top <= y and
                                    x + 127 <= left + TILE_SIZE[0] and
                                    y + 108 <= top + TILE_SIZE[1]
                                    for left, top in TILES))

    def test_tiny_sliver_is_omitted(self):
        self.assertIsNone(tile_box((383, 20, 390, 30), 0, 0))


if __name__ == "__main__":
    unittest.main()
