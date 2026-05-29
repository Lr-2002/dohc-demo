from __future__ import annotations

import json
import unittest

import numpy as np

from dohc_two_monitor_import import load_generator


generator = load_generator()


class GeneratorTests(unittest.TestCase):
    def test_frame_number(self) -> None:
        self.assertEqual(generator.frame_number("frame_162"), 162)
        self.assertIsNone(generator.frame_number("camera_162"))
        self.assertIsNone(generator.frame_number("frame_bad"))

    def test_parse_pose(self) -> None:
        sample = generator.parse_pose(
            7,
            json.dumps(
                {
                    "position": [1, 2, 3],
                    "velocity": [4, 5, 6],
                    "euler": [0.1, 0.2, 0.3],
                    "confidence": 2,
                }
            ),
        )
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(sample.frame, 7)
        self.assertEqual(sample.position, (1.0, 2.0, 3.0))
        self.assertEqual(sample.velocity, (4.0, 5.0, 6.0))
        self.assertEqual(sample.euler, (0.1, 0.2, 0.3))
        self.assertEqual(sample.confidence, 2)

    def test_parse_pose_rejects_incomplete_payload(self) -> None:
        self.assertIsNone(generator.parse_pose(1, json.dumps({"position": [1, 2]})))

    def test_angular_velocity_uses_frame_spacing(self) -> None:
        samples = [
            generator.PoseSample(0, (0, 0, 0), (0, 0, 0), (0.0, 0.0, 0.0), None),
            generator.PoseSample(2, (0, 0, 0), (0, 0, 0), (2.0, 4.0, 6.0), None),
            generator.PoseSample(4, (0, 0, 0), (0, 0, 0), (4.0, 8.0, 12.0), None),
        ]
        np.testing.assert_allclose(generator.angular_velocity(samples), [[1.0, 2.0, 3.0]] * 3)

    def test_static_chart_payload_downsamples_and_keeps_last_sample(self) -> None:
        samples = [
            generator.PoseSample(
                frame,
                (frame * 0.1, frame * 0.2, frame * 0.3),
                (frame * 1.0, frame * 2.0, frame * 3.0),
                (frame * 0.01, frame * 0.02, frame * 0.03),
                None,
            )
            for frame in range(10)
        ]
        angular = generator.angular_velocity(samples)

        payload = generator.static_chart_payload(samples, angular, max_points=4)

        self.assertEqual(payload["frame"], [0, 3, 6, 9])
        self.assertEqual(payload["velocity_xyz"][1], [3.0, 6.0, 9.0])
        self.assertEqual(payload["position_xy"][-1], [0.9, 1.8])

    def test_decimation_stride_rejects_invalid_limit(self) -> None:
        with self.assertRaises(ValueError):
            generator.decimation_stride(10, 0)


if __name__ == "__main__":
    unittest.main()
