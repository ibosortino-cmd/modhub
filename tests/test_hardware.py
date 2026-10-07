import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hardware

GB = 1024 ** 3


class GpuTierTests(unittest.TestCase):
    TABLE = {
        "NVIDIA GeForce RTX 5090": 5, "NVIDIA GeForce RTX 5050": 4, "NVIDIA GeForce RTX 4090": 5, "NVIDIA GeForce RTX 4060 Laptop GPU": 5,
        "NVIDIA GeForce RTX 4050 Laptop GPU": 4, "NVIDIA GeForce RTX 3080 Ti": 5, "NVIDIA GeForce RTX 3070": 5,
        "NVIDIA GeForce RTX 3060 Ti": 5, "NVIDIA GeForce RTX 3060": 4, "NVIDIA GeForce RTX 3050": 4,
        "NVIDIA GeForce RTX 2080": 5, "NVIDIA GeForce RTX 2060": 4, "NVIDIA GeForce GTX 1660 Ti": 3, "NVIDIA GeForce GTX 1650": 3,
        "NVIDIA GeForce GTX 1080 Ti": 4, "NVIDIA GeForce GTX 1070": 4, "NVIDIA GeForce GTX 1060 6GB": 3, "NVIDIA GeForce GTX 1050 Ti": 2,
        "NVIDIA GeForce GTX 970": 3, "NVIDIA GeForce GTX 750 Ti": 2, "NVIDIA GeForce GT 710": 1,
        "AMD Radeon RX 9070 XT": 5, "AMD Radeon RX 7900 XTX": 5, "AMD Radeon RX 7800 XT": 5, "AMD Radeon RX 7600": 4,
        "AMD Radeon RX 6800 XT": 5, "AMD Radeon RX 6700 XT": 5, "AMD Radeon RX 6600": 4, "AMD Radeon RX 6500 XT": 3,
        "AMD Radeon RX 5700 XT": 4, "AMD Radeon RX 5500 XT": 3, "AMD Radeon RX 580 Series": 3, "AMD Radeon RX 550": 2,
        "AMD Radeon RX Vega 64": 4, "AMD Radeon(TM) Vega 8 Graphics": 2, "AMD Radeon(TM) Graphics": 2, "AMD Radeon VII": 5,
        "Intel(R) Arc(TM) A770 Graphics": 4, "Intel(R) Arc(TM) A380 Graphics": 3, "Intel(R) Iris(R) Xe Graphics": 2,
        "Intel(R) UHD Graphics 630": 1, "Intel(R) HD Graphics 4600": 1,
    }

    def test_known_models(self):
        wrong = {n: (hardware.gpu_tier(n), t) for n, t in self.TABLE.items() if hardware.gpu_tier(n) != t}
        self.assertEqual(wrong, {})

    def test_unknown_and_non_gpu_names_are_not_guessed(self):
        for name in ("", None, "Microsoft Basic Display Adapter", "Parsec Virtual Display Adapter"):
            self.assertIsNone(hardware.gpu_tier(name), name)

    def test_nvidia_rtx_is_not_mistaken_for_amd_rx(self):
        self.assertEqual(hardware.gpu_tier("NVIDIA GeForce RTX 3060"), 4)


class ClassifyTests(unittest.TestCase):
    def test_best_card_wins_over_the_integrated_one(self):
        tier, known = hardware.classify(["Intel(R) UHD Graphics 630", "NVIDIA GeForce RTX 3060"], 12, 32)
        self.assertEqual((tier, known), (4, True))

    def test_low_video_memory_and_low_ram_pull_the_tier_down(self):
        self.assertEqual(hardware.classify(["NVIDIA GeForce RTX 3060"], 2, 32)[0], 3)
        self.assertEqual(hardware.classify(["NVIDIA GeForce RTX 3060"], 12, 4)[0], 3)
        self.assertEqual(hardware.classify(["Intel(R) UHD Graphics 630"], None, 4)[0], 1)

    def test_unrecognised_hardware_gets_a_neutral_tier(self):
        self.assertEqual(hardware.classify(["Mystery Adapter"], None, 16), (3, False))
        self.assertEqual(hardware.classify([], None, None), (3, False))


class BuildTests(unittest.TestCase):
    RAW = {"gpus": [{"name": "NVIDIA GeForce RTX 3060", "adapterRam": 4294967295},
                    {"name": "Intel(R) UHD Graphics 630", "adapterRam": 1073741824}],
           "vram": [{"desc": "NVIDIA GeForce RTX 3060", "bytes": 12 * GB}],
           "cpu": {"name": "AMD Ryzen 5 5600X 6-Core Processor", "cores": 6, "threads": 12}, "ram": 32 * GB}

    def test_summary_uses_exact_vram_not_the_4gb_capped_value(self):
        hw = hardware.build(self.RAW)
        self.assertEqual((hw["gpu"], hw["vramGb"], hw["ramGb"], hw["tier"], hw["tierName"], hw["recognised"]),
                         ("NVIDIA GeForce RTX 3060", 12.0, 32.0, 4, "Alta", True))
        self.assertEqual((hw["cpu"], hw["cores"], hw["threads"]), ("AMD Ryzen 5 5600X 6-Core Processor", 6, 12))
        self.assertEqual(len(hw["gpus"]), 2)

    def test_single_items_and_missing_data_do_not_break_it(self):
        hw = hardware.build({"gpus": {"name": "Intel(R) UHD Graphics 630", "adapterRam": 1 * GB}, "vram": {}, "ram": 8 * GB})
        self.assertEqual((hw["gpu"], hw["vramGb"], hw["tier"]), ("Intel(R) UHD Graphics 630", 1.0, 1))
        empty = hardware.build({})
        self.assertEqual((empty["gpu"], empty["tier"], empty["recognised"]), ("Non rilevata", 3, False))

    def test_recommended_level_follows_the_games_mapping(self):
        optimizer = {"levels": [{}] * 5, "byTier": [1, 1, 2, 4, 5]}
        self.assertEqual([hardware.recommended_level({"tier": t}, optimizer) for t in range(1, 6)], [1, 1, 2, 4, 5])
        plain = {"levels": [{}] * 3}  # no mapping -> tier maps straight onto the levels, clamped
        self.assertEqual([hardware.recommended_level({"tier": t}, plain) for t in (1, 5)], [1, 3])

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_real_detection_returns_a_usable_summary(self):
        hw = hardware.detect()
        self.assertIn(hw["tier"], range(1, 6))
        self.assertTrue(hw["gpu"] and hw["cpu"])
        self.assertTrue(hw["ramGb"] is None or hw["ramGb"] > 0)


if __name__ == "__main__":
    unittest.main()
