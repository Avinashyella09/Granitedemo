"""
Phase 7B: the multi-face reconciliation engine.

PURE UNIT TESTS. No database, no Atlas, no mongomock, no HTTP client, no PDF,
no dashboard, no device. The module under test has no Django dependency, so
these are plain SimpleTestCase cases exercising arithmetic and rules only.

    ./venv/bin/python manage.py test blocks.tests_face_reconciliation

Do not run the legacy blocks/tests.py.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from blocks import face_reconciliation as fr


def face(index, dimension_value, height, points=None):
    """Build one face payload using the dimension name that face actually owns."""
    spec = fr.FACE_BY_INDEX[index]
    payload = {"face_index": index, f"{spec.dimension}_m": dimension_value, "height_m": height}
    if points is not None:
        payload["points"] = points
    return payload


def four_faces(l1=1.50, b2=0.90, l3=1.50, b4=0.90, h=(0.85, 0.85, 0.85, 0.85)):
    """A complete, agreeing capture unless a caller perturbs it."""
    return [
        face(0, l1, h[0]),
        face(1, b2, h[1]),
        face(2, l3, h[2]),
        face(3, b4, h[3]),
    ]


# A tolerance set with round numbers, so boundary cases are exact and obvious.
EXACT = fr.Tolerances(
    absolute_floor_m=Decimal("0.030"),
    relative_fraction=Decimal("0"),          # absolute-only, keeps the maths plain
    calibration_status=fr.CALIBRATION_PENDING,
)


class FacePlanTests(SimpleTestCase):
    def test_face_plan_matches_the_approved_order(self):
        self.assertEqual(
            [(s.index, s.label, s.dimension) for s in fr.FACE_PLAN],
            [(0, "Front", "length"), (1, "Right Side", "breadth"),
             (2, "Back", "length"), (3, "Left Side", "breadth")],
        )

    def test_dimension_face_groups_are_derived_not_duplicated(self):
        self.assertEqual(fr.LENGTH_FACES, (0, 2))
        self.assertEqual(fr.BREADTH_FACES, (1, 3))
        self.assertEqual(fr.HEIGHT_FACES, (0, 1, 2, 3))


class ToleranceInterfaceTests(SimpleTestCase):
    # ---- 28: tolerance metadata is carried in the result --------------------

    def test_default_tolerances_are_marked_calibration_pending(self):
        self.assertEqual(fr.DEFAULT_TOLERANCES.calibration_status, fr.CALIBRATION_PENDING)

    def test_result_carries_tolerance_metadata_and_the_pending_warning(self):
        result = fr.reconcile(four_faces())
        used = result["tolerances_used"]
        self.assertEqual(used["calibration_status"], fr.CALIBRATION_PENDING)
        self.assertIn("PLACEHOLDER", used["note"])
        self.assertIn("not field-validated", used["note"])
        self.assertIn("absolute_floor_m", used)
        self.assertIn("relative_fraction", used)

    def test_tolerances_are_injectable_and_actually_applied(self):
        faces = four_faces(l1=1.50, l3=1.56)          # 60 mm apart
        loose = fr.Tolerances(absolute_floor_m=Decimal("0.100"),
                              relative_fraction=Decimal("0"))
        tight = fr.Tolerances(absolute_floor_m=Decimal("0.010"),
                              relative_fraction=Decimal("0"))
        self.assertEqual(fr.reconcile(faces, loose)["status"], fr.RECONCILED)
        self.assertEqual(fr.reconcile(faces, tight)["status"], fr.INCONSISTENT)

    def test_tolerance_is_the_larger_of_absolute_and_relative(self):
        tol = fr.Tolerances(absolute_floor_m=Decimal("0.020"),
                            relative_fraction=Decimal("0.02"))
        self.assertEqual(tol.tolerance_for(Decimal("0.5")), Decimal("0.020"))   # floor wins
        self.assertEqual(tol.tolerance_for(Decimal("3.0")), Decimal("0.060"))   # relative wins

    def test_tolerance_reference_is_order_independent(self):
        """Swapping which face is listed first must not change agreement."""
        a = fr.reconcile(four_faces(l1=1.50, l3=1.53), EXACT)
        b = fr.reconcile([face(2, 1.50, 0.85), face(1, 0.90, 0.85),
                          face(0, 1.53, 0.85), face(3, 0.90, 0.85)], EXACT)
        self.assertEqual(a["status"], b["status"])
        self.assertEqual(a["final_length_m"], b["final_length_m"])


class LengthBreadthReconciliationTests(SimpleTestCase):
    # ---- 1, 2, 4: agreement -------------------------------------------------

    def test_matching_lengths_reconcile(self):
        result = fr.reconcile(four_faces(l1=1.50, l3=1.50), EXACT)
        self.assertEqual(result["status"], fr.RECONCILED)
        self.assertEqual(result["final_length_m"], 1.50)
        self.assertEqual(result["length_gap_m"], 0.0)
        self.assertEqual(result["length"]["rule"], fr.RULE_MEAN_OF_2)

    def test_matching_breadths_reconcile(self):
        result = fr.reconcile(four_faces(b2=0.90, b4=0.90), EXACT)
        self.assertEqual(result["final_breadth_m"], 0.90)
        self.assertEqual(result["breadth_gap_m"], 0.0)

    def test_length_within_tolerance_uses_the_mean(self):
        result = fr.reconcile(four_faces(l1=1.50, l3=1.52), EXACT)
        self.assertEqual(result["status"], fr.RECONCILED)
        self.assertEqual(result["final_length_m"], 1.51)          # exact mean
        self.assertAlmostEqual(result["length_gap_m"], 0.02, places=10)

    def test_breadth_within_tolerance_uses_the_mean(self):
        result = fr.reconcile(four_faces(b2=0.90, b4=0.92), EXACT)
        self.assertEqual(result["final_breadth_m"], 0.91)

    # ---- 5, 6: the exact boundary ------------------------------------------

    def test_length_exactly_at_tolerance_is_accepted(self):
        """The whole reason this module uses Decimal. In float,
        1.53 - 1.50 == 0.030000000000000027, so an observation sitting exactly
        on the boundary would be rejected by an arithmetic artefact."""
        result = fr.reconcile(four_faces(l1=1.50, l3=1.53), EXACT)   # gap == 0.030
        self.assertEqual(result["status"], fr.RECONCILED)
        self.assertEqual(result["final_length_m"], 1.515)
        self.assertEqual(result["length"]["gap_m"], result["length"]["tolerance_m"])

    def test_float_would_have_failed_that_boundary(self):
        """Pins WHY Decimal is used - if this ever stops being true the
        boundary test above has lost its meaning."""
        self.assertNotEqual(1.53 - 1.50, 0.03)
        self.assertFalse((1.53 - 1.50) <= 0.03)
        self.assertTrue((Decimal("1.53") - Decimal("1.50")) <= Decimal("0.03"))

    def test_length_just_inside_tolerance_is_accepted(self):
        result = fr.reconcile(four_faces(l1=1.50, l3=1.5299), EXACT)
        self.assertEqual(result["status"], fr.RECONCILED)

    def test_length_just_outside_tolerance_is_rejected(self):
        result = fr.reconcile(four_faces(l1=1.50, l3=1.5301), EXACT)
        self.assertEqual(result["status"], fr.INCONSISTENT)
        self.assertIsNone(result["final_length_m"])

    def test_breadth_outside_tolerance_is_rejected(self):
        result = fr.reconcile(four_faces(b2=0.90, b4=1.10), EXACT)
        self.assertEqual(result["status"], fr.INCONSISTENT)
        self.assertIsNone(result["final_breadth_m"])

    # ---- naming the disagreement -------------------------------------------

    def test_rejection_names_both_faces_and_the_gap(self):
        result = fr.reconcile(four_faces(l1=1.50, l3=1.62), EXACT)
        reason = " ".join(result["blocking_reasons"])
        self.assertIn("Front", reason)
        self.assertIn("Back", reason)
        self.assertIn("1.5000", reason)
        self.assertIn("1.6200", reason)
        self.assertIn("0.1200", reason)
        self.assertIn("Re-measure", reason)

    def test_both_observations_are_preserved_on_rejection(self):
        result = fr.reconcile(four_faces(l1=1.50, l3=1.62), EXACT)
        values = [o["value_m"] for o in result["length_observations"]]
        self.assertEqual(values, [1.50, 1.62])
        labels = [o["face_label"] for o in result["length_observations"]]
        self.assertEqual(labels, ["Front", "Back"])

    # ---- 26: the core safety property ---------------------------------------

    def test_a_rejected_disagreement_is_never_averaged(self):
        result = fr.reconcile(four_faces(l1=1.50, l3=1.90), EXACT)
        self.assertEqual(result["status"], fr.INCONSISTENT)
        self.assertIsNone(result["final_length_m"])
        self.assertIsNone(result["volume_m3"])
        # 1.70 is the mean that must NOT appear anywhere.
        self.assertNotIn(1.70, [result["final_length_m"], result["volume_m3"]])
        self.assertIsNone(result["_decimal"]["final_length_m"])


class HeightReconciliationTests(SimpleTestCase):
    # ---- 3, 9: the median rule ----------------------------------------------

    def test_matching_heights_reconcile(self):
        result = fr.reconcile(four_faces(h=(0.85, 0.85, 0.85, 0.85)), EXACT)
        self.assertEqual(result["final_height_m"], 0.85)
        self.assertEqual(result["height_spread_m"], 0.0)
        self.assertEqual(result["height"]["rule"], fr.RULE_MEDIAN_OF_4)

    def test_height_uses_the_median_not_the_mean(self):
        """0.85 / 0.85 / 0.86 / 0.88 -> median 0.855, mean 0.86.
        The median is what protects the volume from the high face."""
        result = fr.reconcile(four_faces(h=(0.85, 0.85, 0.86, 0.88)), EXACT)
        self.assertEqual(result["status"], fr.RECONCILED)
        self.assertEqual(result["final_height_m"], 0.855)
        self.assertNotEqual(result["final_height_m"], 0.86)

    # ---- 10, 11: the spread boundary ---------------------------------------

    def test_height_exactly_at_spread_tolerance_is_accepted(self):
        result = fr.reconcile(four_faces(h=(0.85, 0.85, 0.86, 0.88)), EXACT)  # spread 0.030
        self.assertEqual(result["height"]["spread_m"], result["height"]["tolerance_m"])
        self.assertEqual(result["status"], fr.RECONCILED)

    def test_height_just_outside_spread_tolerance_is_rejected(self):
        result = fr.reconcile(four_faces(h=(0.85, 0.85, 0.86, 0.8801)), EXACT)
        self.assertEqual(result["status"], fr.INCONSISTENT)
        self.assertIsNone(result["final_height_m"])

    # ---- 12, 13: outliers ---------------------------------------------------

    def test_one_high_outlier_is_rejected_and_named(self):
        result = fr.reconcile(four_faces(h=(0.85, 0.85, 0.85, 1.10)), EXACT)
        self.assertEqual(result["status"], fr.INCONSISTENT)
        self.assertEqual([o["face_label"] for o in result["height"]["outliers"]], ["Left Side"])
        self.assertIn("Left Side", " ".join(result["blocking_reasons"]))

    def test_one_low_outlier_is_rejected_and_named(self):
        result = fr.reconcile(four_faces(h=(0.85, 0.85, 0.85, 0.40)), EXACT)
        self.assertEqual(result["status"], fr.INCONSISTENT)
        self.assertEqual([o["face_label"] for o in result["height"]["outliers"]], ["Left Side"])

    def test_two_conflicting_heights_are_rejected(self):
        result = fr.reconcile(four_faces(h=(0.85, 0.85, 1.20, 1.20)), EXACT)
        self.assertEqual(result["status"], fr.INCONSISTENT)
        self.assertIsNone(result["volume_m3"])

    def test_every_height_observation_reports_its_deviation(self):
        result = fr.reconcile(four_faces(h=(0.85, 0.85, 0.86, 0.88)), EXACT)
        observations = result["height"]["observations"]
        self.assertEqual(len(observations), 4)
        for entry in observations:
            self.assertIn("deviation_from_median_m", entry)
            self.assertIn("face_label", entry)

    # ---- 27 ------------------------------------------------------------------

    def test_a_rejected_height_spread_is_never_averaged(self):
        result = fr.reconcile(four_faces(h=(0.85, 0.85, 0.85, 1.50)), EXACT)
        self.assertIsNone(result["final_height_m"])
        self.assertIsNone(result["volume_m3"])
        self.assertIsNone(result["_decimal"]["final_height_m"])


class VolumeGatingTests(SimpleTestCase):
    # ---- 21 ------------------------------------------------------------------

    def test_volume_is_calculated_only_after_all_four_faces_pass(self):
        result = fr.reconcile(four_faces(l1=1.50, b2=0.90, l3=1.50, b4=0.90,
                                         h=(0.85, 0.85, 0.85, 0.85)), EXACT)
        self.assertEqual(result["status"], fr.RECONCILED)
        self.assertAlmostEqual(result["volume_m3"], 1.50 * 0.90 * 0.85, places=12)
        self.assertEqual(result["_decimal"]["volume_m3"],
                         Decimal("1.50") * Decimal("0.90") * Decimal("0.85"))

    def test_volume_equals_the_product_of_the_reconciled_dimensions(self):
        result = fr.reconcile(four_faces(l1=1.50, l3=1.52, b2=0.90, b4=0.92,
                                         h=(0.85, 0.85, 0.86, 0.86)), EXACT)
        self.assertEqual(result["status"], fr.RECONCILED)
        expected = (Decimal("1.51") * Decimal("0.91") * Decimal("0.855"))
        self.assertEqual(result["_decimal"]["volume_m3"], expected)

    # ---- 19, 20 --------------------------------------------------------------

    def test_volume_unavailable_on_incomplete_data(self):
        for count in (0, 1, 2, 3):
            with self.subTest(faces=count):
                result = fr.reconcile(four_faces()[:count], EXACT)
                self.assertEqual(result["status"], fr.INCOMPLETE)
                self.assertIsNone(result["volume_m3"])

    def test_volume_unavailable_on_inconsistent_data(self):
        for faces in (four_faces(l1=1.50, l3=1.90),
                      four_faces(b2=0.90, b4=1.40),
                      four_faces(h=(0.85, 0.85, 0.85, 1.40))):
            with self.subTest(case=str(faces)[:40]):
                result = fr.reconcile(faces, EXACT)
                self.assertEqual(result["status"], fr.INCONSISTENT)
                self.assertIsNone(result["volume_m3"])

    def test_one_failing_dimension_blocks_the_volume_even_if_others_pass(self):
        result = fr.reconcile(four_faces(l1=1.50, l3=1.50, b2=0.90, b4=1.50), EXACT)
        self.assertEqual(result["final_length_m"], 1.50)   # length still reconciled
        self.assertIsNone(result["final_breadth_m"])
        self.assertIsNone(result["volume_m3"])             # volume still refused


class IncompleteMeasurementTests(SimpleTestCase):
    # ---- 15, 16, 17, 18 -----------------------------------------------------

    def test_each_missing_face_is_reported_by_name(self):
        for missing_index, label in ((0, "Front"), (1, "Right Side"),
                                     (2, "Back"), (3, "Left Side")):
            with self.subTest(missing=label):
                faces = [f for f in four_faces()
                         if f["face_index"] != missing_index]
                result = fr.reconcile(faces, EXACT)
                self.assertEqual(result["status"], fr.INCOMPLETE)
                self.assertIsNone(result["volume_m3"])
                self.assertEqual(
                    [m["label"] for m in result["faces_missing"]], [label])
                self.assertIn(label, " ".join(result["blocking_reasons"]))

    def test_incomplete_is_a_workflow_state_not_an_exception(self):
        """An officer two faces in is mid-workflow, not in error - a UI needs a
        result it can render progress from."""
        result = fr.reconcile(four_faces()[:2], EXACT)
        self.assertEqual(result["status"], fr.INCOMPLETE)
        self.assertEqual(result["faces_captured"], [0, 1])
        self.assertEqual(len(result["faces_missing"]), 2)

    def test_partial_observations_are_still_echoed_back(self):
        result = fr.reconcile(four_faces()[:2], EXACT)
        self.assertEqual(len(result["length_observations"]), 1)
        self.assertEqual(len(result["height_observations"]), 2)

    def test_no_faces_at_all(self):
        result = fr.reconcile([], EXACT)
        self.assertEqual(result["status"], fr.INCOMPLETE)
        self.assertIsNone(result["volume_m3"])
        self.assertEqual(len(result["faces_missing"]), 4)


class InputValidationTests(SimpleTestCase):
    # ---- 22, 23 --------------------------------------------------------------

    def test_non_finite_values_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(fr.FaceReconciliationError) as ctx:
                    fr.reconcile(four_faces(l1=bad), EXACT)
                self.assertEqual(ctx.exception.code, "value_not_finite")

    def test_zero_and_negative_values_are_rejected(self):
        for bad in (0, -1.5, -0.001):
            with self.subTest(value=bad):
                with self.assertRaises(fr.FaceReconciliationError) as ctx:
                    fr.reconcile(four_faces(l1=bad), EXACT)
                self.assertEqual(ctx.exception.code, "value_not_positive")

    def test_zero_and_negative_heights_are_rejected(self):
        with self.assertRaises(fr.FaceReconciliationError) as ctx:
            fr.reconcile(four_faces(h=(0.85, 0, 0.85, 0.85)), EXACT)
        self.assertEqual(ctx.exception.code, "value_not_positive")

    def test_non_numeric_values_are_rejected(self):
        for bad in ("abc", None, [], {}, True):
            with self.subTest(value=bad):
                with self.assertRaises(fr.FaceReconciliationError) as ctx:
                    fr.reconcile(four_faces(l1=bad), EXACT)
                self.assertEqual(ctx.exception.code, "value_not_numeric")

    def test_numeric_strings_are_accepted(self):
        """The payload will arrive as JSON over multipart eventually."""
        result = fr.reconcile(four_faces(l1="1.50", l3="1.50"), EXACT)
        self.assertEqual(result["final_length_m"], 1.50)

    def test_unknown_face_index_is_rejected(self):
        faces = four_faces()
        faces[0]["face_index"] = 7
        with self.assertRaises(fr.FaceReconciliationError) as ctx:
            fr.reconcile(faces, EXACT)
        self.assertEqual(ctx.exception.code, "face_index_unknown")

    def test_duplicate_face_index_is_rejected(self):
        faces = four_faces()
        faces[2]["face_index"] = 0
        with self.assertRaises(fr.FaceReconciliationError) as ctx:
            fr.reconcile(faces, EXACT)
        self.assertEqual(ctx.exception.code, "face_duplicated")

    def test_more_than_four_faces_is_rejected(self):
        with self.assertRaises(fr.FaceReconciliationError) as ctx:
            fr.reconcile(four_faces() + [face(0, 1.5, 0.85)], EXACT)
        self.assertEqual(ctx.exception.code, "too_many_faces")

    def test_a_face_sending_the_wrong_dimension_is_rejected(self):
        """Face 1 owns breadth. Sending length_m must not be silently
        reconciled as the wrong quantity."""
        faces = four_faces()
        faces[1] = {"face_index": 1, "length_m": 0.90, "height_m": 0.85}
        with self.assertRaises(fr.FaceReconciliationError) as ctx:
            fr.reconcile(faces, EXACT)
        self.assertEqual(ctx.exception.code, "face_dimension_missing")

    def test_a_face_missing_height_is_rejected(self):
        faces = four_faces()
        del faces[0]["height_m"]
        with self.assertRaises(fr.FaceReconciliationError) as ctx:
            fr.reconcile(faces, EXACT)
        self.assertEqual(ctx.exception.code, "face_height_missing")

    def test_malformed_container_is_rejected(self):
        for bad, code in ((None, "faces_not_a_sequence"),
                          ("faces", "faces_not_a_sequence"),
                          ({"face_index": 0}, "faces_not_a_sequence"),
                          ([1, 2, 3], "face_not_a_mapping")):
            with self.subTest(value=bad):
                with self.assertRaises(fr.FaceReconciliationError) as ctx:
                    fr.reconcile(bad, EXACT)
                self.assertEqual(ctx.exception.code, code)

    def test_missing_face_index_is_rejected(self):
        faces = four_faces()
        del faces[0]["face_index"]
        with self.assertRaises(fr.FaceReconciliationError) as ctx:
            fr.reconcile(faces, EXACT)
        self.assertEqual(ctx.exception.code, "face_index_missing")


class CrossFaceWarningTests(SimpleTestCase):
    """Front and Back length edges should be near-parallel and about a breadth
    apart. WARN ONLY - it must never block."""

    def geometry(self, front_edge, back_edge):
        return [
            face(0, 1.50, 0.85, points=front_edge),
            face(1, 0.90, 0.85),
            face(2, 1.50, 0.85, points=back_edge),
            face(3, 0.90, 0.85),
        ]

    # ---- 24 ------------------------------------------------------------------

    def test_cross_face_warning_never_blocks(self):
        """Deliberately perpendicular edges: the warning fires, the volume
        is still produced."""
        faces = self.geometry(
            front_edge=[[0, 0, 0], [1.5, 0, 0], [0, 0.85, 0]],
            back_edge=[[0, 0, 0.9], [0, 0, 2.4], [0, 0.85, 0.9]],   # 90 degrees off
        )
        result = fr.reconcile(faces, EXACT)
        self.assertEqual(result["status"], fr.RECONCILED)
        self.assertIsNotNone(result["volume_m3"])
        self.assertFalse(result["cross_face"]["blocking"])
        self.assertFalse(result["cross_face"]["near_parallel"])
        self.assertTrue(result["warnings"])
        self.assertEqual(result["blocking_reasons"], [])

    def test_parallel_edges_a_breadth_apart_raise_no_warning(self):
        faces = self.geometry(
            front_edge=[[0, 0, 0], [1.5, 0, 0], [0, 0.85, 0]],
            back_edge=[[0, 0, 0.9], [1.5, 0, 0.9], [0, 0.85, 0.9]],
        )
        result = fr.reconcile(faces, EXACT)
        cross = result["cross_face"]
        self.assertEqual(cross["status"], fr.CROSS_FACE_AVAILABLE)
        self.assertTrue(cross["near_parallel"])
        self.assertAlmostEqual(cross["midpoint_separation_m"], 0.9, places=9)
        self.assertAlmostEqual(cross["separation_gap_m"], 0.0, places=9)
        self.assertEqual(cross["warnings"], [])

    def test_the_same_face_measured_twice_is_flagged(self):
        """Identical geometry for Front and Back: every per-face check passes,
        and only the cross-face check can notice."""
        edge = [[0, 0, 0], [1.5, 0, 0], [0, 0.85, 0]]
        result = fr.reconcile(self.geometry(edge, edge), EXACT)
        self.assertEqual(result["status"], fr.RECONCILED)     # still not blocking
        self.assertAlmostEqual(result["cross_face"]["midpoint_separation_m"], 0.0, places=9)
        self.assertIn("same face may have been measured twice",
                      " ".join(result["cross_face"]["warnings"]))

    def test_no_points_means_unavailable_not_invented(self):
        result = fr.reconcile(four_faces(), EXACT)
        cross = result["cross_face"]
        self.assertEqual(cross["status"], fr.CROSS_FACE_UNAVAILABLE)
        self.assertIsNone(cross["parallelism"])
        self.assertIsNone(cross["midpoint_separation_m"])
        self.assertIn("Nothing has been inferred", cross["reason"])

    def test_points_may_be_dicts_as_well_as_triples(self):
        front = [{"x": 0, "y": 0, "z": 0}, {"x": 1.5, "y": 0, "z": 0},
                 {"x": 0, "y": 0.85, "z": 0}]
        back = [{"x": 0, "y": 0, "z": 0.9}, {"x": 1.5, "y": 0, "z": 0.9},
                {"x": 0, "y": 0.85, "z": 0.9}]
        result = fr.reconcile(self.geometry(front, back), EXACT)
        self.assertTrue(result["cross_face"]["near_parallel"])

    def test_malformed_points_are_rejected(self):
        with self.assertRaises(fr.FaceReconciliationError) as ctx:
            fr.reconcile(self.geometry([[0, 0]], [[0, 0, 0]]), EXACT)
        self.assertEqual(ctx.exception.code, "point_malformed")


class DeterminismTests(SimpleTestCase):
    # ---- 25 ------------------------------------------------------------------

    def test_repeated_calculation_is_identical(self):
        faces = four_faces(l1=1.503, l3=1.517, b2=0.901, b4=0.899,
                           h=(0.851, 0.853, 0.852, 0.850))
        first = fr.reconcile(faces, EXACT)
        for _ in range(20):
            self.assertEqual(fr.reconcile(faces, EXACT), first)

    def test_result_does_not_depend_on_face_ordering(self):
        ordered = four_faces(l1=1.50, l3=1.52, b2=0.90, b4=0.92,
                             h=(0.85, 0.86, 0.85, 0.86))
        shuffled = [ordered[2], ordered[0], ordered[3], ordered[1]]
        a, b = fr.reconcile(ordered, EXACT), fr.reconcile(shuffled, EXACT)
        for key in ("status", "final_length_m", "final_breadth_m",
                    "final_height_m", "volume_m3"):
            self.assertEqual(a[key], b[key], key)

    def test_evidence_is_not_rounded_away(self):
        """Observations are echoed exactly as supplied - the caller can always
        recompute from the raw readings."""
        result = fr.reconcile(four_faces(l1=1.234567, l3=1.234567), EXACT)
        self.assertEqual(
            [o["value_m"] for o in result["length_observations"]],
            [1.234567, 1.234567],
        )
        self.assertEqual(result["final_length_m"], 1.234567)


class ResultContractTests(SimpleTestCase):
    def test_result_exposes_every_documented_key(self):
        result = fr.reconcile(four_faces(), EXACT)
        for key in ("status", "final_length_m", "final_breadth_m", "final_height_m",
                    "volume_m3", "length_observations", "breadth_observations",
                    "height_observations", "length_gap_m", "breadth_gap_m",
                    "height_spread_m", "tolerances_used", "rules_applied",
                    "blocking_reasons", "warnings", "cross_face",
                    "faces_captured", "faces_missing", "face_plan"):
            with self.subTest(key=key):
                self.assertIn(key, result)

    def test_rules_applied_names_the_actual_rules(self):
        result = fr.reconcile(four_faces(), EXACT)
        self.assertEqual(result["rules_applied"], {
            "length": fr.RULE_MEAN_OF_2,
            "breadth": fr.RULE_MEAN_OF_2,
            "height": fr.RULE_MEDIAN_OF_4,
        })

    def test_the_same_keys_are_present_in_every_status(self):
        """A caller must not have to branch on status to read the result."""
        reconciled = fr.reconcile(four_faces(), EXACT)
        inconsistent = fr.reconcile(four_faces(l1=1.5, l3=2.5), EXACT)
        incomplete = fr.reconcile(four_faces()[:2], EXACT)
        self.assertEqual(set(reconciled), set(inconsistent))
        self.assertEqual(set(reconciled), set(incomplete))

    def test_blocking_reasons_is_empty_only_when_reconciled(self):
        self.assertEqual(fr.reconcile(four_faces(), EXACT)["blocking_reasons"], [])
        self.assertTrue(fr.reconcile(four_faces(l1=1.5, l3=2.5), EXACT)["blocking_reasons"])
        self.assertTrue(fr.reconcile(four_faces()[:3], EXACT)["blocking_reasons"])

    def test_all_three_dimensions_report_independently(self):
        result = fr.reconcile(four_faces(l1=1.5, l3=2.5, b2=0.9, b4=1.9,
                                         h=(0.85, 0.85, 0.85, 1.85)), EXACT)
        self.assertEqual(len(result["blocking_reasons"]), 3)
        self.assertEqual(result["length"]["status"], fr.INCONSISTENT)
        self.assertEqual(result["breadth"]["status"], fr.INCONSISTENT)
        self.assertEqual(result["height"]["status"], fr.INCONSISTENT)


class PurityTests(SimpleTestCase):
    """The module must stay free of Django, MongoDB and I/O."""

    def test_module_imports_nothing_from_the_project_or_django(self):
        import ast

        source = open(fr.__file__).read()
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level:
                self.fail("relative import found - module must stay standalone")
        self.assertEqual(imported, {"decimal", "dataclasses"}, imported)

    def test_module_is_not_wired_into_any_production_path(self):
        """Phase 7B is the engine only. Nothing may import it yet."""
        import pathlib

        blocks_dir = pathlib.Path(fr.__file__).parent
        importers = []
        for path in blocks_dir.rglob("*.py"):
            if path.name in ("face_reconciliation.py", "tests_face_reconciliation.py"):
                continue
            if "face_reconciliation" in path.read_text():
                importers.append(path.name)
        self.assertEqual(importers, [], f"unexpectedly wired into: {importers}")
