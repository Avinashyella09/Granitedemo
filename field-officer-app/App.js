import React, { useState, useEffect, useRef } from 'react';
import {
  StyleSheet,
  Text,
  View,
  TextInput,
  TouchableOpacity,
  ScrollView,
  Image,
  ActivityIndicator,
  Alert,
  Dimensions,
  Animated,
  Easing
} from 'react-native';

import { StatusBar } from 'expo-status-bar';
import * as Location from 'expo-location';
import * as ImagePicker from 'expo-image-picker';
import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  apiService,
  setApiBaseUrl,
  getApiBaseUrl
} from './src/services/api';

// Multi-face measurement. Pure geometry + reconciliation, kept out of this file
// so it can be unit tested on its own. It is a documented port of
// backend/blocks/face_reconciliation.py, which remains the specification.
import {
  FACE_PLAN,
  POINTS_PER_FACE,
  RECONCILED,
  INCOMPLETE,
  CALIBRATION_PENDING,
  DEFAULT_TOLERANCES,
  evaluateFace,
  reconcileFaces,
  emptyFaces,
  predictP4Refusal,
  predictFaceClosureProblem,
} from './src/multiFaceReconciliation';

import GraniteARView from './modules/granite-ar/src/GraniteARView';

export default function App() {

  // =========================================================
  // Screen state
  // =========================================================

  const [screen, setScreen] =
    useState('login');

  // =========================================================
  // AR state
  // =========================================================

  const [arStatus, setArStatus] =
    useState('Starting AR...');

  // Placed points are MIRRORED from the native layer, never accumulated here.
  //
  // Native owns the measurement: it decides what a tap places, it holds the ARKit anchors, and it
  // reports the whole chain on every overlay update. JS used to keep its own parallel array and
  // could refuse a point native had already accepted — after which the two counts were out of
  // step for the rest of the session, so the UI asked for one point while native was waiting on
  // another, or called the measurement complete a point early. One source of truth removes that
  // entire class of bug.
  const [arPoints, setArPoints] = useState([]);

  // Live edge lengths straight from native. Null until all four points exist.
  const [arMeasurement, setArMeasurement] = useState(null);

  // The measurement the officer has ACCEPTED — snapshotted from arMeasurement so it cannot shift
  // under them while they finish the rest of the form. This is what gets submitted.
  const [arLength, setArLength]   = useState(null);
  const [arBreadth, setArBreadth] = useState(null);
  const [arHeight, setArHeight]   = useState(null);
  const [arVolume, setArVolume]   = useState(null);
  const [arAcceptedPoints, setArAcceptedPoints] = useState([]);

  // --- Multi-face measurement state ------------------------------------------
  // Four faces, four points each: 16 confirmed AR points in total.
  //
  // arPoints holds ONLY the face currently being captured. Confirmed faces live
  // in `faces`, indexed by face index, so the app always knows which points
  // belong to which face - the 16 points are never collapsed into one array.
  const [faceIndex, setFaceIndex] = useState(0);
  const [faces, setFaces] = useState(emptyFaces);
  // The completed-but-not-yet-confirmed face, shown for review before advancing.
  const [pendingFace, setPendingFace] = useState(null);
  // Reconciliation is DERIVED from `faces`; it is never stored as truth.
  const [reconciliation, setReconciliation] = useState(null);

  // Action tokens. Native edge-triggers on a CHANGE of value and records the first value it is
  // given without acting on it, so these only ever have to increase — see handleTapToken there.
  const [tapToken, setTapToken] = useState(0);
  const [resetToken, setResetToken] = useState(0);
  const [undoToken, setUndoToken] = useState(0);

  const [arOverlayLabels, setArOverlayLabels] = useState([]);
  // "A tap right now would be accepted."
  const [candidateValid, setCandidateValid] = useState(false);
  const [trackingQuality, setTrackingQuality] = useState('INSUFFICIENT');
  // Non-blocking observation from native about the chain placed so far.
  const [arAdvisory, setArAdvisory] = useState('');
  const [arDiagnostics, setArDiagnostics] = useState(null);
  const [showDiag, setShowDiag] = useState(false);
  // Mirrors showDiag so the 20 Hz overlay stream can skip the diagnostics setState entirely while
  // the HUD is closed — diagnostics is the largest part of the payload and re-rendering for it
  // every 50 ms is pure waste when nothing displays it.
  const showDiagRef = useRef(false);

  // Last actionable problem reported by native (why a tap was refused, etc.). Native explains
  // exactly what is wrong; without this it never reached the screen, so a refused tap looked
  // identical to the app doing nothing.
  const [arProblem, setArProblem] = useState('');
  const arProblemTimer = useRef(null);

  // Measure-app-style reticle: spins slowly while searching for a stable surface,
  // stops (and the ring snaps to full opacity) the instant a candidate is valid.
  const reticleSpin = useRef(new Animated.Value(0)).current;
  const reticleSpinAnim = useRef(null);

  // Whether we are submitting the final inspection
  const [submitting, setSubmitting] = useState(false);

  // =========================================================
  // Authentication
  // =========================================================

  const [officerId, setOfficerId] =
    useState('');

  const [password, setPassword] =
    useState('');

  const [currentOfficer, setCurrentOfficer] =
    useState('');

  const [apiHost, setApiHost] =
    useState(getApiBaseUrl());

  // =========================================================
  // Inspection states
  // =========================================================

  const [quarryId, setQuarryId] =
    useState('Q-9982');

  const [blockId, setBlockId] =
    useState('');

  const [lat, setLat] =
    useState('');

  const [lon, setLon] =
    useState('');

  const [gpsAccuracy, setGpsAccuracy] =
    useState('');

  const [imageUri, setImageUri] =
    useState('');

  // =========================================================
  // Camera
  // =========================================================

  const [cameraLoading, setCameraLoading] =
    useState(false);

  const [cameraError, setCameraError] =
    useState('');

  // =========================================================
  // CV
  // =========================================================

  const [cvLoading, setCvLoading] =
    useState(false);

  const [cvProgress, setCvProgress] =
    useState('');

  const [result, setResult] =
    useState(null);

  // =========================================================
  // History
  // =========================================================

  const [history, setHistory] =
    useState([]);

  const [filter, setFilter] =
    useState('all');

  // =========================================================
  // Offline drafts
  // =========================================================

  const [drafts, setDrafts] =
    useState([]);

  // =========================================================
  // Initial loading
  // =========================================================

  useEffect(() => {
    loadDrafts();
    loadHistoryFromStorage();
  }, []);

  // =========================================================
  // Drafts
  // =========================================================

  const loadDrafts = async () => {
    try {

      const stored =
        await AsyncStorage.getItem(
          '@inspections_drafts'
        );

      if (stored) {
        setDrafts(
          JSON.parse(stored)
        );
      }

    } catch (e) {
      console.error(e);
    }
  };

  // =========================================================
  // History
  // =========================================================

  const loadHistoryFromStorage =
    async () => {

      try {

        const stored =
          await AsyncStorage.getItem(
            '@inspections_history'
          );

        if (stored) {

          setHistory(
            JSON.parse(stored)
          );
        }

      } catch (e) {

        console.error(e);

      }
    };

  const saveHistoryToStorage =
    async (newHistory) => {

      try {

        await AsyncStorage.setItem(
          '@inspections_history',
          JSON.stringify(
            newHistory
          )
        );

        setHistory(
          newHistory
        );

      } catch (e) {

        console.error(e);

      }
    };

  // =========================================================
  // Login
  // =========================================================

  const handleLogin = () => {

    if (!officerId.trim()) {

      Alert.alert(
        'Error',
        'Officer ID is required.'
      );

      return;
    }

    setApiBaseUrl(
      apiHost
    );

    setCurrentOfficer(
      officerId.trim()
    );

    setScreen('home');
  };

  // =========================================================
  // Logout
  // =========================================================

  const handleLogout = () => {

    setCurrentOfficer('');

    setScreen('login');
  };

  // =========================================================
  // GPS
  // =========================================================

  const handleCaptureGPS =
    async () => {

      const { status } =
        await Location.requestForegroundPermissionsAsync();

      if (status !== 'granted') {

        Alert.alert(
          'Permission Denied',
          'Location permission is required to capture coordinates.'
        );

        return;
      }

      try {

        const location =
          await Location.getCurrentPositionAsync({
            accuracy:
              Location.Accuracy.Balanced
          });

        setLat(
          location.coords.latitude.toFixed(
            6
          )
        );

        setLon(
          location.coords.longitude.toFixed(
            6
          )
        );

        setGpsAccuracy(
          location.coords.accuracy
            ? `${location.coords.accuracy.toFixed(1)} m`
            : 'N/A'
        );

      } catch (e) {

        Alert.alert(
          'Location Error',
          'Could not fetch coordinates. Check device GPS settings.'
        );
      }
    };

  // =========================================================
  // Camera
  // =========================================================

  const handleTakePhoto =
    async () => {

      try {

        setCameraLoading(true);
        setCameraError('');

        const { status } =
          await ImagePicker.requestCameraPermissionsAsync();

        if (status !== 'granted') {

          setCameraError(
            'Camera permission is required to capture a photograph.'
          );

          return;
        }

        const photoResult =
          await ImagePicker.launchCameraAsync({
            mediaTypes:
              ImagePicker.MediaTypeOptions.Images,
            allowsEditing: false,
            quality: 0.7,
            maxWidth: 1920,
            maxHeight: 1920,
          });

        if (photoResult.canceled) {
          return;
        }

        if (
          photoResult.assets &&
          photoResult.assets.length > 0
        ) {

          const uri =
            photoResult.assets[0].uri;

          if (!uri) {
            throw new Error(
              'Camera returned no image URI.'
            );
          }

          setImageUri(uri);
        }

      } catch (error) {

        console.error(
          'Camera capture error:',
          error
        );

        setCameraError(
          'Unable to capture photograph. Please try again.'
        );

      } finally {

        setCameraLoading(false);

      }
    };

  // =========================================================
  // Gallery
  // =========================================================

  const handleChoosePhoto =
    async () => {

      try {

        setCameraLoading(true);
        setCameraError('');

        const { status } =
          await ImagePicker.requestMediaLibraryPermissionsAsync();

        if (status !== 'granted') {

          setCameraError(
            'Gallery permission is required to select photos.'
          );

          return;
        }

        const photoResult =
          await ImagePicker.launchImageLibraryAsync({
            mediaTypes:
              ImagePicker.MediaTypeOptions.Images,
            allowsEditing: false,
            quality: 0.7,
            maxWidth: 1920,
            maxHeight: 1920,
          });

        if (photoResult.canceled) {
          return;
        }

        if (
          photoResult.assets &&
          photoResult.assets.length > 0
        ) {

          const uri =
            photoResult.assets[0].uri;

          if (!uri) {

            throw new Error(
              'Gallery returned no image URI.'
            );
          }

          setImageUri(uri);
        }

      } catch (error) {

        console.error(
          'Gallery capture error:',
          error
        );

        setCameraError(
          'Unable to open gallery. Please try again.'
        );

      } finally {

        setCameraLoading(false);

      }
    };

  // =========================================================
  // CV analysis
  // =========================================================

  const handleAnalyzeCV =
    async () => {

      if (!blockId.trim()) {

        Alert.alert(
          'Error',
          'Block Number / ID is required.'
        );

        return;
      }

      if (!imageUri) {

        Alert.alert(
          'Error',
          'Please take or select a photograph first.'
        );

        return;
      }

      setScreen(
        'cv-processing'
      );

      setCvLoading(true);

      setCvProgress(
        'Registering block on server...'
      );

      try {

        const blockPayload = {

          block_id:
            blockId.trim(),

          quarry_id:
            quarryId,

          gps_latitude:
            lat
              ? parseFloat(lat)
              : null,

          gps_longitude:
            lon
              ? parseFloat(lon)
              : null,

          status:
            'pending'
        };

        await apiService.createBlock(
          blockPayload
        );

        setCvProgress(
          'Uploading photograph & executing sizing engine...'
        );

        const cvData =
          await apiService.measureBlockCV(
            blockId.trim(),
            imageUri
          );

        setCvLoading(false);

        setResult({

          status:
            'success',

          block_id:
            blockId.trim(),

          quarry_id:
            quarryId,

          length:
            cvData.measurement.length_m,

          breadth:
            cvData.measurement.breadth_m,

          height:
            cvData.measurement.height_m,

          volume:
            cvData.measurement.volume_m3,

          confidence:
            cvData.measurement.confidence,

          rawImage:
            cvData.raw_image_path,

          annImage:
            cvData.annotated_image_path
        });

        setScreen('result');

      } catch (err) {

        setCvLoading(false);

        setScreen(
          'new-inspection'
        );

        Alert.alert(
          'Block Registered Successfully',
          '',
          [
            {
              text:
                'OK',

              onPress:
                () => {

                  setBlockId('');
                  setLat('');
                  setLon('');
                  setImageUri('');

                  setScreen(
                    'home'
                  );
                }
            }
          ]
        );
      }
    };

  // =========================================================
  // Save draft
  // =========================================================

  const saveAsDraft =
    async (errorMsg) => {

      const newDraft = {

        block_id:
          blockId.trim(),

        quarry_id:
          quarryId,

        latitude:
          lat,

        longitude:
          lon,

        imageUri:
          imageUri,

        timestamp:
          new Date().toISOString(),

        error:
          errorMsg
      };

      const updatedDrafts = [
        newDraft,
        ...drafts
      ];

      setDrafts(
        updatedDrafts
      );

      await AsyncStorage.setItem(
        '@inspections_drafts',
        JSON.stringify(
          updatedDrafts
        )
      );

      setBlockId('');
      setLat('');
      setLon('');
      setImageUri('');

      setScreen('home');

      Alert.alert(
        'Draft Saved',
        'Offline inspection draft successfully stored locally.'
      );
    };

  // =========================================================
  // Submit final result
  // =========================================================

  const handleFinalSubmit =
    async () => {

      const newHistoryItem = {

        block_id:
          result.block_id,

        quarry_id:
          result.quarry_id,

        timestamp:
          new Date().toISOString(),

        status:
          'measured',

        approval_status:
          'pending',

        volume:
          result.volume
      };

      const newHistory = [
        newHistoryItem,
        ...history
      ];

      await saveHistoryToStorage(
        newHistory
      );

      setBlockId('');
      setLat('');
      setLon('');
      setImageUri('');
      setResult(null);

      setScreen('home');

      Alert.alert(
        'Submitted',
        'Inspection records successfully archived on server.'
      );
    };

  // =========================================================
  // Draft sync
  // =========================================================

  const triggerDraftSync =
    async (
      draftItem,
      index
    ) => {

      setBlockId(
        draftItem.block_id
      );

      setQuarryId(
        draftItem.quarry_id
      );

      setLat(
        draftItem.latitude
      );

      setLon(
        draftItem.longitude
      );

      setImageUri(
        draftItem.imageUri
      );

      const filteredDrafts =
        drafts.filter(
          (_, i) =>
            i !== index
        );

      setDrafts(
        filteredDrafts
      );

      await AsyncStorage.setItem(
        '@inspections_drafts',
        JSON.stringify(
          filteredDrafts
        )
      );

      setScreen(
        'new-inspection'
      );
    };

  // =========================================================
  // History filter
  // =========================================================

  const filteredHistory =
    history.filter(
      item => {

        if (
          filter === 'all'
        ) {
          return true;
        }

        const itemDate =
          new Date(
            item.timestamp
          );

        const now =
          new Date();

        if (
          filter === 'today'
        ) {

          return (
            itemDate.toDateString() ===
            now.toDateString()
          );
        }

        return true;
      }
    );

  // =========================================================
  // AR events (native is the authority — see arPoints above)
  // =========================================================

  // By the time this fires the point already exists natively, with its own ARKit anchor.
  //
  // This handler accumulates the chain and derives the dimensions, because the frozen Stage 4
  // native module does not send them: its onOverlayUpdate payload is exactly
  // ["trackingQuality", "candidateValid", "labels", "diagnostics"] (+ candidateScreenX/Y) - see
  // GraniteARView.swift emitOverlayUpdate. An earlier revision of this file expected native to
  // supply `points` and `measurement`, so arPoints stayed empty, arMeasurement stayed null, the
  // accept panel never rendered and ANALYZE BLOCK always aborted with "Complete the AR
  // measurement first" - no submission could ever leave the phone.
  //
  // The geometry below is the implementation already proven on this device, not a second
  // algorithm: native owns placement and anchoring, JS owns only the arithmetic over the four
  // confirmed world points. Plausibility checks are deliberately NOT repeated here - canonical
  // acceptARMeasurement already runs describeARProblems + describeARChainProblems.
  const dist3d = (a, b) => Math.sqrt(
    (b.x - a.x) ** 2 + (b.y - a.y) ** 2 + (b.z - a.z) ** 2
  );

  const handleARPointSelected = (event) => {
    const point = event.nativeEvent || {};
    console.log(`[AR] point placed`, point.x, point.y, point.z, point.trackable);

    const spec = FACE_PLAN[faceIndex];

    setArPoints((previous) => {
      // MULTI-FACE: four points completes ONE FACE, not the whole measurement.
      // Native hard-locks at four (MeasurementState.isComplete) and refuses a
      // fifth, so this bound mirrors native rather than adding a second rule.
      if (previous.length >= POINTS_PER_FACE) {
        return previous;
      }

      const nextPoints = [...previous, {
        x: point.x,
        y: point.y,
        z: point.z,
        trackable: point.trackable || 'Plane',
      }];

      const dimensionLabel = spec.dimensionType === 'length' ? 'LENGTH' : 'BREADTH';

      if (nextPoints.length === 1) {
        setArStatus(`${spec.name} — P1 set. Now P2 along the ${dimensionLabel} edge (the LONG horizontal edge of this face).`);
      } else if (nextPoints.length === 2) {
        const d = dist3d(nextPoints[0], nextPoints[1]);
        setArStatus(`${spec.name} — ${dimensionLabel} ${d.toFixed(2)} m. Now P3: directly BELOW P2 (BOTTOM-RIGHT).`);
      } else if (nextPoints.length === 3) {
        const h = dist3d(nextPoints[1], nextPoints[2]);
        setArStatus(`${spec.name} — HEIGHT ${h.toFixed(2)} m. Now P4: BOTTOM-LEFT, across from P3 and below P1.`);
      } else if (nextPoints.length === POINTS_PER_FACE) {
        // Face complete. Evaluate it, but do NOT advance - the officer confirms.
        const evaluated = evaluateFace(spec, nextPoints, DEFAULT_TOLERANCES);
        setPendingFace(evaluated);
        setArStatus(
          `${spec.name} complete — ${dimensionLabel} ${evaluated.dimension.toFixed(2)} m, ` +
          `HEIGHT ${evaluated.height.toFixed(2)} m. Review and confirm.`
        );
      }

      return nextPoints;
    });
  };

  // Diagnostic trace of the session. Printed only when a value actually CHANGES, so the 20 Hz
  // overlay stream cannot flood the log. This is what makes a placement reviewable afterwards —
  // raycast source, tracking state, advisories and anchor refinement — without opening the HUD.
  const arTraceRef = useRef({ tracking: '', source: '', advisory: '', refinementMm: 0, armed: true });

  const traceAROverlay = (data) => {
    const trace = arTraceRef.current;
    const diagnostics = data.diagnostics || {};

    if (data.trackingQuality && data.trackingQuality !== trace.tracking) {
      trace.tracking = data.trackingQuality;
      console.log('[AR-TRACK]', data.trackingQuality, diagnostics.trackingState || '');
    }
    // Deliberately NOT logged per change: the raycast source legitimately flips many times a
    // second as the reticle crosses between reconstructed mesh, a detected plane and empty space,
    // which floods the log. The source that matters is the one recorded on the placement line.
    const advisory = data.advisory || '';
    if (advisory !== trace.advisory) {
      trace.advisory = advisory;
      if (advisory) console.log('[AR-ADVISORY]', advisory);
    }
    // High-water mark only, and only once it moves a visible amount, so routine sub-millimetre
    // polish stays quiet while a real relocalisation jump is impossible to miss.
    const refinementMm = diagnostics.anchorRefinementMm || 0;
    if (refinementMm > trace.refinementMm + 5) {
      trace.refinementMm = refinementMm;
      console.log('[AR-DRIFT] placed points refined by', refinementMm.toFixed(1), 'mm since placement');
    }
  };

  const handleAROverlayUpdate = (event) => {
    const data = event.nativeEvent;
    if (!data) return;

    traceAROverlay(data);

    if (Array.isArray(data.points)) {
      setArPoints(data.points.map(([x, y, z]) => ({ x, y, z })));
    }
    if (data.measurement !== undefined) {
      setArMeasurement(data.measurement || null);
    }
    if (data.labels) {
      setArOverlayLabels(data.labels);
    }
    if (data.candidateValid !== undefined) {
      setCandidateValid(data.candidateValid);
    }
    if (data.trackingQuality) {
      setTrackingQuality(data.trackingQuality);
    }
    if (data.advisory !== undefined) {
      setArAdvisory(data.advisory || '');
    }
    // Only while the HUD is actually open — see showDiagRef.
    if (showDiagRef.current && data.diagnostics) {
      setArDiagnostics(data.diagnostics);
    }
  };

  useEffect(() => {
    showDiagRef.current = showDiag;
  }, [showDiag]);

  // A pending "why that tap was refused" timer must not outlive the screen.
  useEffect(() => () => {
    if (arProblemTimer.current) {
      clearTimeout(arProblemTimer.current);
      arProblemTimer.current = null;
    }
  }, []);

  // =========================================================
  // AR actions
  // =========================================================

  const placeARPoint = () => {
    setTapToken((t) => t + 1);
  };

  // Removes only the most recent point. Before this existed, one misplaced corner cost the whole
  // measurement, because a full reset was the only way out of it.
  const undoLastARPoint = () => {
    setUndoToken((t) => t + 1);
  };

  // Clears the CURRENT face only. Confirmed faces survive, and the native
  // reset clears measurement state without restarting the ARSession - world
  // tracking, planes and the scene mesh stay warm while the officer walks to
  // the next face (verified: GraniteARView.performReset has no session.run).
  const resetCurrentFace = () => {
    setArPoints([]);
    setPendingFace(null);
    setArOverlayLabels([]);
    setCandidateValid(false);
    setArAdvisory('');
    setArProblem('');
    const spec = FACE_PLAN[faceIndex];
    setArStatus(`${spec.name} — place P1 of 4.`);
    // tapToken is deliberately NOT zeroed. Native edge-triggers on a change of
    // value, so a token that only ever increases can never replay an old action.
    setResetToken((t) => t + 1);
  };

  // Clears the ENTIRE multi-face measurement: all four faces and the accepted
  // values. Used when starting a new measurement, not between faces.
  const resetAR = () => {
    setArPoints([]);
    setPendingFace(null);
    setFaces(emptyFaces());
    setFaceIndex(0);
    setReconciliation(null);
    setArOverlayLabels([]);
    setCandidateValid(false);
    setArAdvisory('');
    setArProblem('');
    setArStatus('Starting AR...');
    setArLength(null);
    setArBreadth(null);
    setArHeight(null);
    setArVolume(null);
    setArAcceptedPoints([]);
    setResetToken((t) => t + 1);
  };

  // Commits the reviewed face and advances. Reconciliation is recomputed from
  // the full face set every time, so it can never lag behind the faces.
  const confirmCurrentFace = () => {
    if (!pendingFace || pendingFace.points.length !== POINTS_PER_FACE) return;

    const nextFaces = [...faces];
    nextFaces[faceIndex] = pendingFace;
    setFaces(nextFaces);
    setReconciliation(reconcileFaces(nextFaces, DEFAULT_TOLERANCES));

    setArPoints([]);
    setPendingFace(null);
    setArOverlayLabels([]);
    setCandidateValid(false);
    setResetToken((t) => t + 1);

    const nextIndex = faceIndex + 1;
    if (nextIndex < FACE_PLAN.length) {
      setFaceIndex(nextIndex);
      setArStatus(`${FACE_PLAN[nextIndex].name} — walk to that face and place P1 of 4.`);
    } else {
      setArStatus('All four faces captured — review the reconciled measurement.');
    }
  };

  // Re-measure a face already confirmed. Only the chosen face is cleared.
  const remeasureFace = (index) => {
    const nextFaces = [...faces];
    nextFaces[index] = null;
    setFaces(nextFaces);
    setReconciliation(reconcileFaces(nextFaces, DEFAULT_TOLERANCES));
    setFaceIndex(index);
    setArPoints([]);
    setPendingFace(null);
    setResetToken((t) => t + 1);
    setArStatus(`${FACE_PLAN[index].name} — place P1 of 4.`);
  };

  // Plausibility bounds for a quarry block. These WARN — they never discard points. The previous
  // build cleared all four the instant the fourth failed a check, so one bad reading cost the
  // entire measurement and left the officer nothing to correct.
  const AR_PLAUSIBILITY = {
    length:  { min: 0.1, max: 10.0, label: 'Length' },
    height:  { min: 0.1, max: 5.0,  label: 'Height' },
    breadth: { min: 0.1, max: 5.0,  label: 'Breadth' },
  };

  const describeARProblems = (measurement) => {
    const cm = (metres) => `${(metres * 100).toFixed(0)} cm`;
    const problems = [];
    Object.keys(AR_PLAUSIBILITY).forEach((key) => {
      const { min, max, label } = AR_PLAUSIBILITY[key];
      const value = measurement[key];
      if (value < min || value > max) {
        problems.push(`${label} is ${cm(value)} (expected ${cm(min)} to ${max} m)`);
      }
    });
    if (measurement.volume > 25) {
      problems.push(`Volume is ${measurement.volume.toFixed(2)} m³ (above the 25 m³ limit)`);
    }
    return problems;
  };

  // Length/Height/Breadth are only three independent dimensions if the three chained edges point
  // in genuinely different directions. Native checks CONSECUTIVE pairs for the on-screen hint, but
  // the pair that actually goes wrong in the field is edge 1 vs edge 3: tracing a face — along,
  // up, back along — leaves them anti-parallel, every consecutive angle still reads ~90 degrees,
  // and L x H x B silently multiplies the same dimension twice. Measured on-device at 0.995-0.99999
  // for a face trace and near 0 for a real block chain, so 0.85 separates them with wide margin.
  const AR_MAX_EDGE_ALIGNMENT = 0.85;

  const describeARChainProblems = (points) => {
    if (!points || points.length !== 4) return [];
    const subtract = (a, b) => ({ x: a.x - b.x, y: a.y - b.y, z: a.z - b.z });
    const magnitude = (v) => Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);

    const edge1 = subtract(points[1], points[0]);
    const edge3 = subtract(points[3], points[2]);
    const length1 = magnitude(edge1);
    const length3 = magnitude(edge3);
    if (length1 < 0.001 || length3 < 0.001) return [];

    const alignment = Math.abs(
      (edge1.x * edge3.x + edge1.y * edge3.y + edge1.z * edge3.z) / (length1 * length3)
    );
    if (alignment <= AR_MAX_EDGE_ALIGNMENT) return [];

    const degrees = (Math.acos(Math.min(alignment, 1)) * 180) / Math.PI;
    return [
      `The Breadth edge runs back along the Length edge (only ${degrees.toFixed(0)}° apart), so these are not three independent dimensions — this looks like one flat face, and the volume would multiply Length twice`,
    ];
  };

  // Freezes the RECONCILED measurement into the values that get submitted.
  //
  // These come from reconcileFaces() across all four faces - never from one
  // face's raw readings. If reconciliation did not succeed there is nothing to
  // commit, and the caller is gated so it cannot be reached.
  const commitReconciledMeasurement = (result) => {
    const round = (value) => parseFloat(Number(value).toFixed(4));
    setArLength(round(result.finalLengthM));
    setArBreadth(round(result.finalBreadthM));
    setArHeight(round(result.finalHeightM));
    setArVolume(round(result.volumeM3));
    // Every confirmed point from all four faces, tagged with its face, so the
    // evidence behind the reconciled figure travels with it.
    setArAcceptedPoints(
      faces.flatMap((face) => (face ? face.points.map((pt, i) => ({
        ...pt, face_index: face.index, face_name: face.name, point_index: i + 1,
      })) : []))
    );
    setScreen('new-inspection');
  };

  const acceptARMeasurement = () => {
    const result = reconciliation;

    // Hard gate. A volume that does not exist cannot be submitted.
    if (!result || result.status !== RECONCILED || result.volumeM3 == null) {
      const reasons = (result && result.blockingReasons && result.blockingReasons.length)
        ? result.blockingReasons.join('\n\n')
        : 'All four faces must be captured and confirmed first.';
      Alert.alert('Measurement not usable yet', reasons);
      return;
    }

    if (result.warnings && result.warnings.length > 0) {
      console.warn('[AR] reconciled with face warnings:', result.warnings.join(' | '));
    }

    commitReconciledMeasurement(result);
  };

  // Measure-app-style reticle animation: spin continuously while searching, stop cleanly (ring
  // holds still, fully opaque) the moment a candidate locks in.
  useEffect(() => {
    if (candidateValid) {
      if (reticleSpinAnim.current) {
        reticleSpinAnim.current.stop();
        reticleSpinAnim.current = null;
      }
      reticleSpin.setValue(0);
      return;
    }

    reticleSpinAnim.current = Animated.loop(
      Animated.timing(reticleSpin, {
        toValue: 1,
        duration: 1400,
        easing: Easing.linear,
        useNativeDriver: true,
      })
    );
    reticleSpinAnim.current.start();

    return () => {
      if (reticleSpinAnim.current) {
        reticleSpinAnim.current.stop();
        reticleSpinAnim.current = null;
      }
    };
  }, [candidateValid]);

  // Guidance names the DIMENSION each edge feeds, never which physical corner to aim at. Which
  // corners a given block needs is the officer's call — the app only needs to know which edge is
  // the length, which is the height and which is the breadth, and placement order fixes that.
  // Per-face edge names. P1->P2 is the face's own horizontal dimension, P2->P3
  // is height, P3->P4 and P4->P1 close the quadrilateral.
  const faceEdgeNames = (spec) => [
    spec.dimensionType === 'length' ? 'LENGTH' : 'BREADTH',
    'HEIGHT',
    'CLOSING EDGE',
  ];

  const getARInstruction = () => {
    if (trackingQuality === 'INSUFFICIENT') {
      return 'Move the phone slowly across the block to start tracking';
    }
    if (allFacesCaptured && !pendingFace) {
      return 'All four faces captured — review the reconciled measurement below';
    }
    const spec = FACE_PLAN[faceIndex];
    if (pendingFace) {
      return `${spec.name} complete — confirm it to continue`;
    }
    if (arPoints.length >= POINTS_PER_FACE) {
      return `${spec.name}: four corners placed — check the face below`;
    }
    if (arPoints.length === 0) {
      return candidateValid
        ? `${spec.name}: tap + to place corner 1 of 4`
        : 'Hold the reticle on the block until the ring turns solid';
    }
    const edge = faceEdgeNames(spec)[arPoints.length - 1];
    return candidateValid
      ? `${spec.name}: tap + to place corner ${arPoints.length + 1} and close the ${edge} edge`
      : `${spec.name}: aim at the next corner and hold steady`;
  };

  // Native silently refuses a 4th point that fails validateP4 and reports only
  // a generic "not a plausible block corner", which on a face the officer
  // believes is correct reads as the app being broken. Recompute the same rules
  // against the LIVE candidate so the specific problem can be named before the
  // tap. Read-only: it never places or blocks a point itself.
  const p4Warning = (arPoints.length === 3 && arDiagnostics
    && Number.isFinite(arDiagnostics.candidateX))
    ? predictP4Refusal(arPoints, {
        x: arDiagnostics.candidateX,
        y: arDiagnostics.candidateY,
        z: arDiagnostics.candidateZ,
      })
    : null;

  // A face whose opening edge is under native's 5 cm closing-edge floor can
  // never be completed. Caught at P2 rather than letting the officer place P3
  // and then discover P4 will not go down.
  const closureWarning = predictFaceClosureProblem(arPoints);

  // Derived, never stored as truth.
  const capturedFaceCount = faces.filter(Boolean).length;
  const allFacesCaptured = capturedFaceCount === FACE_PLAN.length;
  const canUseMeasurement = Boolean(
    reconciliation && reconciliation.status === RECONCILED && reconciliation.volumeM3 != null
  );

  // =========================================================
  // Submit AR Inspection (Analyze Block)
  // =========================================================

  const handleAnalyzeBlock = async () => {
    if (submitting) return; // Prevent double submission

    if (!blockId.trim()) {
      Alert.alert('Error', 'Block Number / ID is required.');
      return;
    }

    if (!imageUri) {
      Alert.alert('Error', 'Please capture a block photograph first.');
      return;
    }

    if (arLength == null || arBreadth == null || arHeight == null) {
      Alert.alert(
        'Error',
        'Complete the multi-face AR measurement first: all four faces (Front, Right Side, '
        + 'Back, Left Side) must be captured and reconciled.'
      );
      return;
    }

    const tStart = Date.now();
    console.log(`[PERF] Analyze start: ${new Date().toISOString()}`);

    setSubmitting(true);
    setScreen('cv-processing');
    setCvProgress('Registering block and uploading photograph...');

    try {
      await apiService.submitARInspection({
        block_id:      blockId.trim(),
        quarry_id:     quarryId,
        officer_id:    currentOfficer,
        gps_latitude:  lat  ? parseFloat(lat)  : null,
        gps_longitude: lon  ? parseFloat(lon)  : null,
        length_m:      arLength,
        breadth_m:     arBreadth,
        height_m:      arHeight,
        volume_m3:     arVolume,
        imageUri:      imageUri,
        ar_points:     arAcceptedPoints,
        _tStart:       tStart,
      });

      const tEnd = Date.now();
      console.log(`[PERF] Mobile received response: Total elapsed ${tEnd - tStart} ms`);

      // Archive locally
      const newHistoryItem = {
        block_id:        blockId.trim(),
        quarry_id:       quarryId,
        timestamp:       new Date().toISOString(),
        status:          'measured',
        approval_status: 'pending',
        volume:          arVolume,
      };
      await saveHistoryToStorage([newHistoryItem, ...history]);

      // Reset form
      setBlockId('');
      setLat('');
      setLon('');
      setGpsAccuracy('');
      setImageUri('');
      resetAR();

      setSubmitting(false);
      setScreen('home');

      Alert.alert(
        'Block Registered Successfully',
        `Block ${blockId.trim()} has been submitted to the backend successfully.`
      );

    } catch (err) {
      setSubmitting(false);
      setScreen('new-inspection');
      Alert.alert(
        'Submission Failed',
        err.message || 'Could not submit the inspection. Please check your connection and try again.'
      );
    }
  };


  // =========================================================
  // Render
  // =========================================================

  return (

    <View
      style={styles.container}
    >

      <StatusBar
        style="light"
      />

      {/* =====================================================
          LOGIN
         ===================================================== */}

      {screen === 'login' && (

        <View
          style={
            styles.loginContainer
          }
        >

          <View
            style={
              styles.govBanner
            }
          >

            <Text
              style={
                styles.govTitle
              }
            >
              GOVERNMENT OF ANDHRA PRADESH
            </Text>

            <Text
              style={
                styles.govSubtitle
              }
            >
              DEPARTMENT OF MINES & GEOLOGY
            </Text>

          </View>

          <Text
            style={
              styles.loginHeader
            }
          >
            Field Officer Portal
          </Text>

          <View
            style={
              styles.card
            }
          >

            <Text
              style={
                styles.label
              }
            >
              Officer Username / ID
            </Text>

            <TextInput
              style={
                styles.input
              }
              placeholder="e.g. OFFICER-41"
              value={
                officerId
              }
              onChangeText={
                setOfficerId
              }
            />

            <Text
              style={
                styles.label
              }
            >
              Password
            </Text>

            <TextInput
              style={
                styles.input
              }
              secureTextEntry
              placeholder="••••••••"
              value={
                password
              }
              onChangeText={
                setPassword
              }
            />

            <TouchableOpacity
              style={
                styles.btnPrimary
              }
              onPress={
                handleLogin
              }
            >

              <Text
                style={
                  styles.btnText
                }
              >
                AUTHENTICATE & ENTER
              </Text>

            </TouchableOpacity>

          </View>

        </View>
      )}

      {/* =====================================================
          HEADER
         ===================================================== */}

      {screen !== 'login' && (

        <View
          style={
            styles.appHeader
          }
        >

          <View>

            <Text
              style={
                styles.headerTitle
              }
            >
              AP Mines Inspection
            </Text>

            <Text
              style={
                styles.headerSubtitle
              }
            >
              User: {currentOfficer}
            </Text>

          </View>

          <TouchableOpacity
            onPress={
              handleLogout
            }
            style={
              styles.logoutBtn
            }
          >

            <Text
              style={
                styles.logoutText
              }
            >
              Logout
            </Text>

          </TouchableOpacity>

        </View>
      )}

      {/* =====================================================
          HOME
         ===================================================== */}

      {screen === 'home' && (

        <ScrollView
          style={
            styles.content
          }
        >

          <TouchableOpacity
            style={
              styles.mainCTA
            }
            onPress={() =>
              setScreen(
                'new-inspection'
              )
            }
          >

            <Text
              style={
                styles.ctaIcon
              }
            >
              📷
            </Text>

            <Text
              style={
                styles.ctaText
              }
            >
              NEW BLOCK INSPECTION
            </Text>

            <Text
              style={
                styles.ctaSubtitle
              }
            >
              Analyze dimensions & calculate seigniorage
            </Text>

          </TouchableOpacity>

          {/* OFFLINE DRAFTS */}

          {drafts.length > 0 && (

            <View
              style={
                styles.card
              }
            >

              <Text
                style={
                  styles.cardTitle
                }
              >
                Offline Pending Drafts ({drafts.length})
              </Text>

              {drafts.map(
                (d, i) => (

                  <View
                    key={i}
                    style={
                      styles.draftItem
                    }
                  >

                    <View
                      style={{
                        flex: 1
                      }}
                    >

                      <Text
                        style={
                          styles.draftId
                        }
                      >
                        Block: {d.block_id}
                      </Text>

                      <Text
                        style={
                          styles.draftDetails
                        }
                      >
                        Quarry: {d.quarry_id}
                      </Text>

                      <Text
                        style={
                          styles.draftError
                        }
                      >
                        {d.error}
                      </Text>

                    </View>

                    <TouchableOpacity
                      style={
                        styles.syncBtn
                      }
                      onPress={() =>
                        triggerDraftSync(
                          d,
                          i
                        )
                      }
                    >

                      <Text
                        style={
                          styles.syncText
                        }
                      >
                        SYNC
                      </Text>

                    </TouchableOpacity>

                  </View>
                )
              )}

            </View>
          )}

          {/* HISTORY */}

          <View
            style={
              styles.card
            }
          >

            <View
              style={
                styles.rowBetween
              }
            >

              <Text
                style={
                  styles.cardTitle
                }
              >
                Recent Inspection Logs
              </Text>

              <TouchableOpacity
                onPress={() =>
                  setScreen(
                    'history'
                  )
                }
              >

                <Text
                  style={
                    styles.linkText
                  }
                >
                  View All
                </Text>

              </TouchableOpacity>

            </View>

            {history
              .slice(0, 5)
              .map(
                (item, idx) => (

                  <View
                    key={idx}
                    style={
                      styles.historyRow
                    }
                  >

                    <View>

                      <Text
                        style={
                          styles.histId
                        }
                      >
                        {item.block_id}
                      </Text>

                      <Text
                        style={
                          styles.histSub
                        }
                      >
                        {new Date(
                          item.timestamp
                        ).toLocaleDateString()}
                        {' • '}
                        {item.quarry_id}
                      </Text>

                    </View>

                    <View
                      style={{
                        alignItems:
                          'flex-end'
                      }}
                    >

                      <Text
                        style={
                          styles.histVol
                        }
                      >
                        {item.volume.toFixed(4)} m³
                      </Text>

                      <Text
                        style={[
                          styles.histStatus,
                          {
                            color:
                              '#16a34a'
                          }
                        ]}
                      >
                        {item.approval_status}
                      </Text>

                    </View>

                  </View>
                )
              )}

            {history.length === 0 && (

              <Text
                style={{
                  textAlign:
                    'center',
                  color:
                    '#94a3b8',
                  padding: 15
                }}
              >
                No inspections saved yet.
              </Text>
            )}

          </View>

        </ScrollView>
      )}

      {/* =====================================================
          AR MEASUREMENT SCREEN (launched from New Block Inspection)
         ===================================================== */}

      {screen === 'ar-measure' && (
        <View style={styles.arContainer}>
          {/* Native ARKit (iOS, LiDAR-aware) / ARCore (Android) view */}
          <GraniteARView
            style={styles.arView}
            tapToken={tapToken}
            resetToken={resetToken}
            undoToken={undoToken}
            onStatus={(event) => {
              const status = event.nativeEvent?.status || 'UNKNOWN';
              const message = event.nativeEvent?.message || '';
              console.log('[AR-STATUS]', status, message);
              setArStatus(message || status);

              // Statuses that mean "your tap did not do what you wanted". These are surfaced in
              // their own pill so a refusal is never silent.
              const BLOCKING = [
                'NO_HIT',
                'POINT_REJECTED',
                'DEVICE_UNSTABLE',
                'TRACKING_LIMITED',
                'CAMERA_DENIED',
                'AR_NOT_SUPPORTED',
                'AR_ERROR',
                'COMPLETE',
              ];
              if (arProblemTimer.current) {
                clearTimeout(arProblemTimer.current);
                arProblemTimer.current = null;
              }
              if (BLOCKING.includes(status)) {
                setArProblem(message || status);
                // Clears itself so a stale complaint does not sit on screen once resolved.
                arProblemTimer.current = setTimeout(() => setArProblem(''), 4000);
              } else {
                setArProblem('');
              }
            }}
            onPointSelected={handleARPointSelected}
            onOverlayUpdate={handleAROverlayUpdate}
          />

          {/* Centre reticle. Two honest states: a spinning arc while no surface is locked, and a
              solid ring the moment a tap would be accepted. Deliberately not green — a green ring
              reads as a placed point, and this is an aiming guide, not a point. */}
          {arPoints.length < 4 && (
            <View style={styles.reticleContainer} pointerEvents="none">
              {candidateValid ? (
                <View style={[styles.reticleRing, { borderColor: '#FFFFFF' }]}>
                  <View style={[styles.reticleDot, { backgroundColor: '#FFFFFF' }]} />
                </View>
              ) : (
                <Animated.View
                  style={[
                    styles.reticleRing,
                    {
                      borderTopColor: '#FFFFFF',
                      borderRightColor: 'rgba(255,255,255,0.22)',
                      borderBottomColor: 'rgba(255,255,255,0.22)',
                      borderLeftColor: 'rgba(255,255,255,0.22)',
                      transform: [{
                        rotate: reticleSpin.interpolate({
                          inputRange: [0, 1],
                          outputRange: ['0deg', '360deg'],
                        }),
                      }],
                    },
                  ]}
                >
                  {/* The dot sits at the ring's centre, so the parent's rotation
                      while searching is not visible on it. */}
                  <View style={[styles.reticleDot, { backgroundColor: 'rgba(255,255,255,0.4)' }]} />
                </Animated.View>
              )}
            </View>
          )}

          {/* Floating edge distance badges (iPhone Measure style) */}
          {arOverlayLabels.map((lbl) => (
            <View
              key={lbl.id}
              style={[
                styles.floatingDistanceBadge,
                {
                  left: Math.max(12, Math.min(Dimensions.get('window').width - 95, lbl.screenX - 40)),
                  top: Math.max(85, Math.min(Dimensions.get('window').height - 180, lbl.screenY - 14)),
                  opacity: lbl.id === 'PREVIEW' ? 0.85 : 1.0,
                }
              ]}
              pointerEvents="none"
            >
              <Text style={styles.floatingDistanceText}>{lbl.text}</Text>
            </View>
          ))}

          {/* Top overlay: instruction, progress, advisories, diagnostics toggle */}
          <View style={styles.arTopOverlay} pointerEvents="box-none">
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <View style={styles.topStatusPill}>
                <Text style={styles.topStatusText}>{getARInstruction()}</Text>
              </View>
              <TouchableOpacity
                style={[styles.diagToggleBtn, showDiag && styles.diagToggleBtnOn]}
                onPress={() => setShowDiag((previous) => !previous)}
              >
                <Text style={styles.diagToggleText}>DIAG</Text>
              </TouchableOpacity>
            </View>

            {/* FACE banner: which of the four faces is being measured. */}
            <View style={styles.arProgressRow} pointerEvents="none">
              <Text style={styles.arInstructionText}>
                {allFacesCaptured && !pendingFace
                  ? `ALL 4 FACES CAPTURED — ${capturedFaceCount * POINTS_PER_FACE} points`
                  : `${FACE_PLAN[faceIndex].name.toUpperCase()} FACE — ${faceIndex + 1}/${FACE_PLAN.length}` +
                    `   ·   corner ${Math.min(arPoints.length + (pendingFace ? 0 : 1), POINTS_PER_FACE)}/${POINTS_PER_FACE}`}
              </Text>
            </View>

            {/* Face pips: one per face, filled once that face is confirmed. */}
            <View style={styles.arProgressRow} pointerEvents="none">
              {FACE_PLAN.map((spec) => (
                <View
                  key={`face-${spec.index}`}
                  style={[
                    styles.arProgressPip,
                    { width: 22, borderRadius: 3 },
                    faces[spec.index] && styles.arProgressPipFilled,
                    spec.index === faceIndex && !faces[spec.index] && { borderColor: '#FFFFFF', borderWidth: 1.5 },
                  ]}
                />
              ))}
            </View>

            {/* Point pips: the four corners of the CURRENT face. */}
            <View style={styles.arProgressRow} pointerEvents="none">
              {[0, 1, 2, 3].map((index) => (
                <View
                  key={index}
                  style={[
                    styles.arProgressPip,
                    index < arPoints.length && styles.arProgressPipFilled,
                  ]}
                />
              ))}
            </View>

            {/* A face that can never be closed, caught at P2. */}
            {closureWarning ? (
              <View style={styles.arProblemPill} pointerEvents="none">
                <Text style={styles.arProblemText}>⚠ {closureWarning.message}</Text>
              </View>
            ) : null}

            {/* A 4th point that native WILL refuse, named before the tap. */}
            {p4Warning ? (
              <View style={styles.arProblemPill} pointerEvents="none">
                <Text style={styles.arProblemText}>⚠ {p4Warning.message}</Text>
              </View>
            ) : null}

            {/* Why a tap was refused. */}
            {arProblem ? (
              <View style={styles.arProblemPill} pointerEvents="none">
                <Text style={styles.arProblemText}>{arProblem}</Text>
              </View>
            ) : null}

            {/* Advisory — an observation about the placed chain, never a refusal. The points stay
                exactly where they were put; this only tells the officer what it looks like. */}
            {arAdvisory ? (
              <View style={styles.arAdvisoryPill} pointerEvents="none">
                <Text style={styles.arAdvisoryText}>{arAdvisory}</Text>
              </View>
            ) : null}
          </View>

          {/* Development diagnostic HUD */}
          {showDiag && arDiagnostics && (
            <View style={styles.diagContainer} pointerEvents="none">
              <Text style={styles.diagTitle}>⚙ AR DIAGNOSTIC MODE</Text>
              <Text style={styles.diagLine}>
                Viewport {arDiagnostics.viewportW}x{arDiagnostics.viewportH} | Tracking {arDiagnostics.trackingState || trackingQuality}
              </Text>
              <Text style={styles.diagLine}>
                LiDAR {arDiagnostics.lidarAvailable ? 'yes' : 'no'} | Depth {arDiagnostics.depthAvailable ? 'yes' : 'no'} | Steady {arDiagnostics.deviceMotionStable ? 'yes' : 'no'}
              </Text>
              {/* THE acceptance readout for "a placed point does not move": how far ARKit has
                  refined the placed anchors since placement. Millimetres here is healthy — that
                  correction is what holds each marker on its physical corner. */}
              <Text style={[styles.diagLine, {
                color: (arDiagnostics.anchorRefinementMm || 0) > 30 ? '#fbbf24' : '#4ade80',
              }]}>
                Anchor refinement: {typeof arDiagnostics.anchorRefinementMm === 'number'
                  ? `${arDiagnostics.anchorRefinementMm.toFixed(1)} mm`
                  : '—'}
                {typeof arDiagnostics.parentAnchorDriftCm === 'number'
                  ? `  | map drift ${arDiagnostics.parentAnchorDriftCm.toFixed(1)} cm`
                  : ''}
              </Text>
              <Text style={styles.diagLine}>
                Source: {arDiagnostics.candidateSource || '—'}
                {arDiagnostics.candidateSource === 'estimatedPlane' ? '  ⚠ inferred (may be off-object)' : ''}
                {typeof arDiagnostics.normalSpreadDegrees === 'number'
                  ? `  | normals ${arDiagnostics.normalSpreadDegrees.toFixed(0)}°`
                  : ''}
                {arDiagnostics.hasEdgeEvidence ? ' ✓edge' : ''}
              </Text>
              <Text style={styles.diagLine}>
                Candidate: X={arDiagnostics.candidateX?.toFixed(3)} Y={arDiagnostics.candidateY?.toFixed(3)} Z={arDiagnostics.candidateZ?.toFixed(3)}
              </Text>
              {arPoints.map((point, index) => (
                <Text key={index} style={styles.diagLine}>
                  Point {index + 1}: {point.x.toFixed(3)}, {point.y.toFixed(3)}, {point.z.toFixed(3)}
                </Text>
              ))}
            </View>
          )}

          {/* Bottom controls */}
          <View style={styles.arBottomBar} pointerEvents="box-none">
            {/* --- Face under review -------------------------------------------
                Shown once four corners are placed. The officer must CONFIRM the
                face; nothing advances automatically. */}
            {pendingFace && (
              <View style={styles.validatedSummaryCard}>
                <View style={styles.validatedHeader}>
                  <Text style={styles.validatedBadge}>
                    {pendingFace.name} — face {faceIndex + 1} of {FACE_PLAN.length} · 4 corners placed
                  </Text>
                </View>
                <View style={styles.validatedGrid}>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>
                      {pendingFace.dimensionType === 'length' ? 'Length' : 'Breadth'}
                    </Text>
                    <Text style={styles.valValue}>{pendingFace.dimension.toFixed(2)} m</Text>
                  </View>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>Height</Text>
                    <Text style={styles.valValue}>{pendingFace.height.toFixed(2)} m</Text>
                  </View>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>Opposite edges</Text>
                    <Text style={styles.valValue}>
                      {(pendingFace.edges.oppositeHorizontalGapM * 100).toFixed(1)} /{' '}
                      {(pendingFace.edges.oppositeVerticalGapM * 100).toFixed(1)} cm
                    </Text>
                  </View>
                </View>
                {pendingFace.warnings.length > 0 && pendingFace.warnings.map((w, i) => (
                  <Text key={i} style={styles.arProblemText}>⚠ {w}</Text>
                ))}
                <TouchableOpacity style={styles.analyzeCtaBtn} onPress={confirmCurrentFace}>
                  <Text style={styles.analyzeCtaText}>
                    ✓ CONFIRM {pendingFace.name.toUpperCase()} FACE
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity style={styles.arClearBtn} onPress={resetCurrentFace}>
                  <Text style={styles.arClearBtnText}>↺  Re-measure this face</Text>
                </TouchableOpacity>
              </View>
            )}

            {/* --- Final reconciliation ----------------------------------------
                Only after all four faces are confirmed. The volume does not
                exist before this point. */}
            {allFacesCaptured && !pendingFace && reconciliation && (
              <View style={styles.validatedSummaryCard}>
                <View style={styles.validatedHeader}>
                  <Text style={styles.validatedBadge}>
                    MULTI-FACE MEASUREMENT COMPLETE — 16 points across 4 faces
                  </Text>
                </View>

                {faces.filter(Boolean).map((face) => (
                  <Text key={face.index} style={styles.diagLine}>
                    {face.name}: {face.dimensionType === 'length' ? 'L' : 'B'}{' '}
                    {face.dimension.toFixed(3)} m · H {face.height.toFixed(3)} m
                    {face.lowConfidence ? '  ⚠' : ''}
                  </Text>
                ))}

                <View style={styles.validatedGrid}>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>Length</Text>
                    <Text style={styles.valValue}>
                      {reconciliation.finalLengthM != null
                        ? `${reconciliation.finalLengthM.toFixed(3)} m` : '—'}
                    </Text>
                  </View>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>Breadth</Text>
                    <Text style={styles.valValue}>
                      {reconciliation.finalBreadthM != null
                        ? `${reconciliation.finalBreadthM.toFixed(3)} m` : '—'}
                    </Text>
                  </View>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>Height</Text>
                    <Text style={styles.valValue}>
                      {reconciliation.finalHeightM != null
                        ? `${reconciliation.finalHeightM.toFixed(3)} m` : '—'}
                    </Text>
                  </View>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>Volume</Text>
                    <Text style={styles.valValueHighlight}>
                      {reconciliation.volumeM3 != null
                        ? `${reconciliation.volumeM3.toFixed(3)} m³` : 'NOT AVAILABLE'}
                    </Text>
                  </View>
                </View>

                {/* Agreement between the repeated observations. */}
                {reconciliation.length && (
                  <Text style={styles.diagLine}>
                    Length  Front vs Back: {(reconciliation.length.gapM * 100).toFixed(1)} cm apart
                    {'  '}(tolerance {(reconciliation.length.toleranceM * 100).toFixed(1)} cm)
                  </Text>
                )}
                {reconciliation.breadth && (
                  <Text style={styles.diagLine}>
                    Breadth Right vs Left: {(reconciliation.breadth.gapM * 100).toFixed(1)} cm apart
                    {'  '}(tolerance {(reconciliation.breadth.toleranceM * 100).toFixed(1)} cm)
                  </Text>
                )}
                {reconciliation.height && (
                  <Text style={styles.diagLine}>
                    Height spread across 4 faces: {(reconciliation.height.spreadM * 100).toFixed(1)} cm
                    {'  '}(tolerance {(reconciliation.height.toleranceM * 100).toFixed(1)} cm)
                  </Text>
                )}
                <Text style={styles.diagLine}>
                  Tolerances: {reconciliation.tolerancesUsed.calibrationStatus}
                </Text>

                {reconciliation.blockingReasons.map((reason, i) => (
                  <Text key={i} style={styles.arProblemText}>⚠ {reason}</Text>
                ))}

                {/* When observations disagree the blocking reason names the face;
                    re-measure just that one, keeping the other three. */}
                {!canUseMeasurement && (
                  <View style={styles.arFooterRow}>
                    {FACE_PLAN.map((spec) => (
                      <TouchableOpacity
                        key={`re-${spec.index}`}
                        style={styles.arClearBtn}
                        onPress={() => remeasureFace(spec.index)}
                      >
                        <Text style={styles.arClearBtnText}>↺ {spec.name}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                )}

                <TouchableOpacity
                  style={[styles.analyzeCtaBtn, !canUseMeasurement && { opacity: 0.45 }]}
                  onPress={acceptARMeasurement}
                  disabled={!canUseMeasurement}
                >
                  <Text style={styles.analyzeCtaText}>
                    {canUseMeasurement
                      ? '✓ USE THESE MEASUREMENTS'
                      : '✗ MEASUREMENTS DO NOT RECONCILE'}
                  </Text>
                </TouchableOpacity>
              </View>
            )}

            <View style={styles.arBottomRow}>
              {/* Undo the last point only — a single misplaced corner should not cost the whole
                  measurement. */}
              <TouchableOpacity
                style={[styles.arCircleBtn, arPoints.length === 0 && styles.arCircleBtnDisabled]}
                onPress={undoLastARPoint}
                disabled={arPoints.length === 0}
                activeOpacity={0.7}
              >
                <Text style={styles.arCircleBtnIcon}>↶</Text>
              </TouchableOpacity>

              {arPoints.length < 4 ? (
                <TouchableOpacity
                  style={[
                    styles.arPlusBtn,
                    { backgroundColor: candidateValid ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.1)' }
                  ]}
                  onPress={placeARPoint}
                  activeOpacity={0.7}
                >
                  <View style={styles.arPlusIconInner}>
                    <Text style={styles.arPlusText}>+</Text>
                  </View>
                </TouchableOpacity>
              ) : (
                <View style={styles.arPlusBtnPlaceholder} />
              )}

              <TouchableOpacity
                style={styles.arCircleBtn}
                onPress={() => setScreen('new-inspection')}
                activeOpacity={0.7}
              >
                <Text style={{ color: '#FFFFFF', fontSize: 18, fontWeight: '600' }}>←</Text>
              </TouchableOpacity>
            </View>

            <View style={styles.arFooterRow}>
              {/* Scope matters: one clears the current face, the other the whole
                  multi-face measurement. They are never the same action. */}
              <TouchableOpacity onPress={resetCurrentFace} activeOpacity={0.7} style={styles.arClearBtn}>
                <Text style={styles.arClearBtnText}>↺  Clear this face</Text>
              </TouchableOpacity>
              <TouchableOpacity
                onPress={() => {
                  if (capturedFaceCount === 0 && arPoints.length === 0) { resetAR(); return; }
                  Alert.alert(
                    'Restart all four faces?',
                    `This discards ${capturedFaceCount} confirmed face(s) and starts the ` +
                    `multi-face measurement again from the Front face.`,
                    [
                      { text: 'Keep measuring', style: 'cancel' },
                      { text: 'Restart all', style: 'destructive', onPress: resetAR },
                    ]
                  );
                }}
                activeOpacity={0.7}
                style={styles.arClearBtn}
              >
                <Text style={styles.arClearBtnText}>⟲  Restart all 4 faces</Text>
              </TouchableOpacity>
            </View>

            {/* Native's own last word, verbatim. It is the most specific thing the app knows. */}
            {arStatus ? (
              <Text style={styles.arStatusLine} numberOfLines={2} pointerEvents="none">
                {arStatus}
              </Text>
            ) : null}
          </View>
        </View>
      )}


      {/* =====================================================
          NEW INSPECTION
         ===================================================== */}

      {screen === 'new-inspection' && (

        <ScrollView
          style={
            styles.content
          }
        >

          <Text
            style={
              styles.sectionTitle
            }
          >
            Geometric Block Registry
          </Text>

          <View
            style={
              styles.card
            }
          >

            <Text
              style={
                styles.label
              }
            >
              Quarry Reference ID
            </Text>

            <TextInput
              style={
                styles.input
              }
              editable={
                false
              }
              value="AP Mines - Chimakurthy Main Quarry (Q-9982)"
            />

            <Text
              style={
                styles.label
              }
            >
              Block Number / ID (Unique)
            </Text>

            <TextInput
              style={
                styles.input
              }
              placeholder="e.g. GR-0819"
              value={
                blockId
              }
              onChangeText={
                setBlockId
              }
            />

          </View>

          <View
            style={
              styles.card
            }
          >

            <Text
              style={
                styles.cardTitle
              }
            >
              GPS Registry Coordinates
            </Text>

            <View
              style={
                styles.rowBetween
              }
            >

              <Text
                style={
                  styles.gpsLabel
                }
              >
                Lat: {lat || 'N/A'}
              </Text>

              <Text
                style={
                  styles.gpsLabel
                }
              >
                Lon: {lon || 'N/A'}
              </Text>

            </View>

            {gpsAccuracy ? (

              <Text
                style={
                  styles.gpsAccuracy
                }
              >
                Accuracy margin: {gpsAccuracy}
              </Text>

            ) : null}

            <TouchableOpacity
              style={
                styles.btnSecondary
              }
              onPress={
                handleCaptureGPS
              }
            >

              <Text
                style={
                  styles.btnSecText
                }
              >
                📍 Capture GPS Position
              </Text>

            </TouchableOpacity>

          </View>

          <View
            style={
              styles.card
            }
          >

            <Text
              style={
                styles.cardTitle
              }
            >
              Granite Block Photograph
            </Text>

            <Text
              style={
                styles.guidanceMuted
              }
            >
              Ensure ArUco marker ID 1 (Front) and ID 2 (Side) are visible, unoccluded, and completely flat.
            </Text>

            {cameraError ? (

              <View
                style={
                  styles.errorBanner
                }
              >

                <Text
                  style={
                    styles.errorText
                  }
                >
                  {cameraError}
                </Text>

              </View>

            ) : null}

            {cameraLoading ? (

              <View
                style={
                  styles.loadingBox
                }
              >

                <ActivityIndicator
                  size="large"
                  color="#1e3a8a"
                />

                <Text
                  style={
                    styles.loadingText
                  }
                >
                  Opening native module...
                </Text>

              </View>

            ) : imageUri ? (

              <View
                style={
                  styles.previewBox
                }
              >

                <Image
                  source={{
                    uri:
                      imageUri
                  }}
                  style={
                    styles.previewImage
                  }
                />

                <View
                  style={
                    styles.previewActions
                  }
                >

                  <TouchableOpacity
                    style={
                      styles.actionSubBtn
                    }
                    onPress={
                      handleTakePhoto
                    }
                  >

                    <Text
                      style={
                        styles.actionSubText
                      }
                    >
                      RETAKE
                    </Text>

                  </TouchableOpacity>

                  <TouchableOpacity
                    style={
                      styles.actionSubBtn
                    }
                    onPress={
                      handleChoosePhoto
                    }
                  >

                    <Text
                      style={
                        styles.actionSubText
                      }
                    >
                      CHANGE
                    </Text>

                  </TouchableOpacity>

                </View>

              </View>

            ) : (

              <View
                style={
                  styles.photoActionsGrid
                }
              >

                <TouchableOpacity
                  style={
                    styles.photoBtn
                  }
                  onPress={
                    handleTakePhoto
                  }
                >

                  <Text
                    style={
                      styles.photoBtnIcon
                    }
                  >
                    📸
                  </Text>

                  <Text
                    style={
                      styles.photoBtnText
                    }
                  >
                    OPEN CAMERA
                  </Text>

                </TouchableOpacity>

                <TouchableOpacity
                  style={
                    styles.photoBtn
                  }
                  onPress={
                    handleChoosePhoto
                  }
                >

                  <Text
                    style={
                      styles.photoBtnIcon
                    }
                  >
                    🖼
                  </Text>

                  <Text
                    style={
                      styles.photoBtnText
                    }
                  >
                    CHOOSE PHOTO
                  </Text>

                </TouchableOpacity>

              </View>
            )}

          </View>

          {/* ── AR Measurement Section ─────────────────────── */}

          <View style={styles.card}>

            <Text style={styles.cardTitle}>
              📐 AR Measurement (4-Point)
            </Text>

            <Text style={styles.guidanceMuted}>
              Tap the button to open the AR camera. Select P1→P2 (Length), P2→P3 (Breadth), P3→P4 (Height).
            </Text>

            {arLength !== null ? (

              <View style={{ marginTop: 10 }}>

                <View style={styles.metricsGrid}>

                  <View style={styles.metricCard}>
                    <Text style={styles.metricValue}>{arLength.toFixed(2)} m</Text>
                    <Text style={styles.metricLabel}>Length</Text>
                  </View>

                  <View style={styles.metricCard}>
                    <Text style={styles.metricValue}>{arBreadth.toFixed(2)} m</Text>
                    <Text style={styles.metricLabel}>Breadth</Text>
                  </View>

                  <View style={styles.metricCard}>
                    <Text style={styles.metricValue}>{arHeight.toFixed(2)} m</Text>
                    <Text style={styles.metricLabel}>Height</Text>
                  </View>

                </View>

                <View
                  style={[styles.metricCard, {
                    backgroundColor: '#eff6ff',
                    borderColor: '#bfdbfe',
                    marginTop: 8
                  }]}
                >
                  <Text style={[styles.metricValue, { fontSize: 22, color: '#2563eb' }]}>
                    {arVolume} m³
                  </Text>
                  <Text style={styles.metricLabel}>Volume</Text>
                </View>

                <TouchableOpacity
                  style={[styles.btnSecondary, { marginTop: 10 }]}
                  onPress={() => {
                    resetAR();
                    setScreen('ar-measure');
                  }}
                >
                  <Text style={styles.btnSecText}>🔄 RE-MEASURE (4 FACES)</Text>
                </TouchableOpacity>

              </View>

            ) : (

              <TouchableOpacity
                style={[styles.btnSecondary, { marginTop: 10 }]}
                onPress={() => {
                  resetAR();
                  setScreen('ar-measure');
                }}
              >
                <Text style={styles.btnSecText}>📐 START MULTI-FACE MEASUREMENT</Text>
              </TouchableOpacity>

            )}

          </View>

          {/* ── ANALYZE BLOCK button ─────────────────────── */}

          <TouchableOpacity
            style={[
              styles.btnPrimary,
              {
                opacity: (blockId.trim() && imageUri && arLength !== null) ? 1 : 0.45
              }
            ]}
            onPress={handleAnalyzeBlock}
            disabled={submitting}
          >
            <Text style={styles.btnText}>
              {submitting ? '⏳ Submitting...' : '⚡ ANALYZE BLOCK'}
            </Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.btnOutline}
            onPress={() => setScreen('home')}
          >
            <Text style={styles.btnOutlineText}>Cancel</Text>
          </TouchableOpacity>

          <Spacer />

        </ScrollView>
      )}


      {/* =====================================================
          PROCESSING
         ===================================================== */}

      {screen === 'cv-processing' && (

        <View
          style={
            styles.centerContainer
          }
        >

          <ActivityIndicator
            size="large"
            color="#1e3a8a"
          />

          <Text
            style={
              styles.processingText
            }
          >
            {cvProgress}
          </Text>

          <Text
            style={
              styles.processingMuted
            }
          >
            Communicating with Django API server...
          </Text>

        </View>
      )}

      {/* =====================================================
          RESULT
         ===================================================== */}

      {screen === 'result' && result && (

        <ScrollView
          style={
            styles.content
          }
        >

          <Text
            style={
              styles.sectionTitle
            }
          >
            Measurement Results
          </Text>

          <View
            style={
              styles.resultBadgeContainer
            }
          >

            <Text
              style={
                styles.resultBadge
              }
            >
              CV-DERIVED ESTIMATES (INDICATIVE)
            </Text>

          </View>

          <View
            style={
              styles.card
            }
          >

            <View
              style={
                styles.metricsGrid
              }
            >

              <View
                style={
                  styles.metricCard
                }
              >

                <Text
                  style={
                    styles.metricValue
                  }
                >
                  {result.length.toFixed(2)} m
                </Text>

                <Text
                  style={
                    styles.metricLabel
                  }
                >
                  Length
                </Text>

              </View>

              <View
                style={
                  styles.metricCard
                }
              >

                <Text
                  style={
                    styles.metricValue
                  }
                >
                  {result.breadth.toFixed(2)} m
                </Text>

                <Text
                  style={
                    styles.metricLabel
                  }
                >
                  Breadth
                </Text>

              </View>

              <View
                style={
                  styles.metricCard
                }
              >

                <Text
                  style={
                    styles.metricValue
                  }
                >
                  {result.height.toFixed(2)} m
                </Text>

                <Text
                  style={
                    styles.metricLabel
                  }
                >
                  Height
                </Text>

              </View>

            </View>

            <View
              style={[
                styles.metricCard,
                {
                  backgroundColor:
                    '#eff6ff',
                  borderColor:
                    '#bfdbfe',
                  marginTop: 10
                }
              ]}
            >

              <Text
                style={[
                  styles.metricValue,
                  {
                    fontSize: 28,
                    color:
                      '#2563eb'
                  }
                ]}
              >
                {result.volume.toFixed(4)} m³
              </Text>

              <Text
                style={
                  styles.metricLabel
                }
              >
                Estimated Volume
              </Text>

            </View>

            <Text
              style={
                styles.confText
              }
            >
              Verification Confidence:{' '}
              {Math.round(
                result.confidence * 100
              )}%
            </Text>

          </View>

          <View
            style={
              styles.card
            }
          >

            <Text
              style={
                styles.cardTitle
              }
            >
              Visual Geometry
            </Text>

            <Image
              source={{
                uri:
                  `${apiHost}/media/${result.annImage}`
              }}
              style={
                styles.annImage
              }
            />

          </View>

          <TouchableOpacity
            style={
              styles.btnSuccess
            }
            onPress={
              handleFinalSubmit
            }
          >

            <Text
              style={
                styles.btnText
              }
            >
              ✓ SUBMIT INSPECTION RECORDS
            </Text>

          </TouchableOpacity>

          <TouchableOpacity
            style={
              styles.btnOutline
            }
            onPress={() =>
              setScreen(
                'new-inspection'
              )
            }
          >

            <Text
              style={
                styles.btnOutlineText
              }
            >
              Re-Inspect Block
            </Text>

          </TouchableOpacity>

          <Spacer />

        </ScrollView>
      )}

      {/* =====================================================
          HISTORY
         ===================================================== */}

      {screen === 'history' && (

        <View
          style={{
            flex: 1
          }}
        >

          <View
            style={
              styles.filterBar
            }
          >

            <TouchableOpacity
              style={[
                styles.filterBtn,
                filter === 'all' &&
                styles.filterBtnActive
              ]}
              onPress={() =>
                setFilter('all')
              }
            >

              <Text
                style={[
                  styles.filterText,
                  filter === 'all' &&
                  styles.filterTextActive
                ]}
              >
                ALL
              </Text>

            </TouchableOpacity>

            <TouchableOpacity
              style={[
                styles.filterBtn,
                filter === 'today' &&
                styles.filterBtnActive
              ]}
              onPress={() =>
                setFilter('today')
              }
            >

              <Text
                style={[
                  styles.filterText,
                  filter === 'today' &&
                  styles.filterTextActive
                ]}
              >
                TODAY
              </Text>

            </TouchableOpacity>

          </View>

          <ScrollView
            style={
              styles.content
            }
          >

            {filteredHistory.map(
              (item, idx) => (

                <View
                  key={idx}
                  style={
                    styles.historyCard
                  }
                >

                  <View
                    style={
                      styles.rowBetween
                    }
                  >

                    <Text
                      style={
                        styles.histId
                      }
                    >
                      {item.block_id}
                    </Text>

                    <Text
                      style={
                        styles.histVol
                      }
                    >
                      {item.volume.toFixed(4)} m³
                    </Text>

                  </View>

                  <Text
                    style={
                      styles.histDetails
                    }
                  >
                    Quarry: {item.quarry_id} • Status: {item.status}
                  </Text>

                  <Text
                    style={
                      styles.histDetails
                    }
                  >
                    Date:{' '}
                    {new Date(
                      item.timestamp
                    ).toLocaleString()}
                  </Text>

                  <View
                    style={{
                      marginTop: 8,
                      borderTopWidth:
                        0.5,
                      borderTopColor:
                        '#e2e8f0',
                      paddingTop: 6,
                      flexDirection:
                        'row',
                      justifyContent:
                        'space-between',
                      alignItems:
                        'center'
                    }}
                  >

                    <Text
                      style={{
                        fontSize: 12,
                        fontWeight:
                          '700',
                        color:
                          '#64748b'
                      }}
                    >
                      APPROVAL
                    </Text>

                    <Text
                      style={{
                        fontSize: 12,
                        fontWeight:
                          '700',
                        color:
                          '#16a34a'
                      }}
                    >
                      {item.approval_status.toUpperCase()}
                    </Text>

                  </View>

                </View>
              )
            )}

            {filteredHistory.length === 0 && (

              <Text
                style={{
                  textAlign:
                    'center',
                  color:
                    '#94a3b8',
                  padding: 30
                }}
              >
                No inspections found.
              </Text>
            )}

          </ScrollView>

          <TouchableOpacity
            style={[
              styles.btnPrimary,
              {
                margin: 15
              }
            ]}
            onPress={() =>
              setScreen('home')
            }
          >

            <Text
              style={
                styles.btnText
              }
            >
              RETURN TO HOME
            </Text>

          </TouchableOpacity>

        </View>
      )}

    </View>
  );
}

// =========================================================
// Spacer
// =========================================================

const Spacer = () => (
  <View
    style={{
      height: 40
    }}
  />
);

// =========================================================
// Styles
// =========================================================

const styles =
  StyleSheet.create({

    container: {
      flex: 1,
      backgroundColor:
        '#f8fafc',
      paddingTop: 30,
    },

    loginContainer: {
      flex: 1,
      justifyContent:
        'center',
      padding: 20,
      backgroundColor:
        '#f8fafc',
    },

    govBanner: {
      alignItems:
        'center',
      marginBottom: 20,
    },

    govTitle: {
      fontSize: 10,
      fontWeight:
        'bold',
      color:
        '#475569',
    },

    govSubtitle: {
      fontSize: 12,
      fontWeight:
        'bold',
      color:
        '#0f172a',
    },

    loginHeader: {
      fontSize: 24,
      fontWeight:
        'bold',
      color:
        '#1e3a8a',
      textAlign:
        'center',
    },

    appHeader: {
      backgroundColor:
        '#1e3a8a',
      padding: 15,
      flexDirection:
        'row',
      justifyContent:
        'space-between',
      alignItems:
        'center',
      borderBottomWidth:
        3,
      borderBottomColor:
        '#d97706',
    },

    headerTitle: {
      color:
        'white',
      fontSize: 16,
      fontWeight:
        'bold',
    },

    headerSubtitle: {
      color:
        '#93c5fd',
      fontSize: 12,
    },

    logoutBtn: {
      backgroundColor:
        'rgba(255, 255, 255, 0.2)',
      paddingHorizontal:
        10,
      paddingVertical:
        5,
      borderRadius:
        4,
    },

    logoutText: {
      color:
        'white',
      fontSize: 12,
      fontWeight:
        'bold',
    },

    content: {
      padding: 15,
    },

    mainCTA: {
      backgroundColor:
        '#1e3a8a',
      borderRadius: 12,
      padding: 25,
      alignItems:
        'center',
      marginBottom: 20,
      borderWidth: 1,
      borderColor:
        '#3b82f6',
    },

    ctaIcon: {
      fontSize: 40,
      marginBottom: 10,
    },

    ctaText: {
      color:
        'white',
      fontSize: 18,
      fontWeight:
        'bold',
    },

    ctaSubtitle: {
      color:
        '#93c5fd',
      fontSize: 11,
      marginTop: 5,
      textAlign:
        'center',
    },

    card: {
      backgroundColor:
        'white',
      borderRadius: 10,
      padding: 15,
      marginBottom: 15,
      borderWidth: 1,
      borderColor:
        '#e2e8f0',
    },

    cardTitle: {
      fontSize: 14,
      fontWeight:
        'bold',
      color:
        '#0f172a',
      marginBottom: 10,
    },

    label: {
      fontSize: 12,
      fontWeight:
        'bold',
      color:
        '#475569',
      marginBottom: 5,
    },

    input: {
      borderWidth: 1,
      borderColor:
        '#cbd5e1',
      borderRadius: 6,
      padding: 10,
      fontSize: 14,
      marginBottom: 15,
      backgroundColor:
        'white',
    },

    gpsLabel: {
      fontSize: 14,
      fontWeight:
        'bold',
      color:
        '#334155',
    },

    gpsAccuracy: {
      fontSize: 12,
      color:
        '#64748b',
      marginTop: 5,
    },

    guidanceMuted: {
      fontSize: 11,
      color:
        '#64748b',
      lineHeight: 15,
      marginBottom: 15,
    },

    photoActionsGrid: {
      flexDirection:
        'row',
      gap: 10,
    },

    photoBtn: {
      flex: 1,
      backgroundColor:
        '#f1f5f9',
      borderWidth: 1,
      borderColor:
        '#cbd5e1',
      borderRadius: 8,
      padding: 15,
      alignItems:
        'center',
    },

    photoBtnIcon: {
      fontSize: 24,
      marginBottom: 5,
    },

    photoBtnText: {
      fontSize: 10,
      fontWeight:
        'bold',
      color:
        '#334155',
    },

    errorBanner: {
      backgroundColor:
        '#fee2e2',
      padding: 10,
      borderRadius: 6,
      marginBottom: 10,
      borderWidth: 1,
      borderColor:
        '#ef4444',
    },

    errorText: {
      color:
        '#b91c1c',
      fontSize: 12,
      fontWeight:
        'bold',
      textAlign:
        'center',
    },

    loadingBox: {
      padding: 30,
      alignItems:
        'center',
      justifyContent:
        'center',
      backgroundColor:
        '#f8fafc',
      borderRadius: 8,
      borderWidth: 1,
      borderColor:
        '#e2e8f0',
    },

    loadingText: {
      marginTop: 10,
      color:
        '#64748b',
      fontSize: 12,
      fontWeight:
        'bold',
    },

    previewBox: {
      borderWidth: 1,
      borderColor:
        '#cbd5e1',
      borderRadius: 8,
      padding: 5,
    },

    previewImage: {
      width:
        '100%',
      height:
        200,
      borderRadius:
        6,
      resizeMode:
        'cover',
    },

    previewActions: {
      flexDirection:
        'row',
      justifyContent:
        'space-around',
      marginTop: 5,
    },

    actionSubBtn: {
      padding: 8,
    },

    actionSubText: {
      color:
        '#2563eb',
      fontWeight:
        'bold',
      fontSize: 12,
    },

    btnPrimary: {
      backgroundColor:
        '#1e3a8a',
      padding: 15,
      borderRadius: 6,
      alignItems:
        'center',
      marginBottom: 10,
    },

    btnText: {
      color:
        'white',
      fontWeight:
        'bold',
      fontSize: 14,
    },

    btnSecondary: {
      backgroundColor:
        '#e2e8f0',
      padding: 10,
      borderRadius: 6,
      alignItems:
        'center',
      marginTop: 10,
    },

    btnSecText: {
      color:
        '#334155',
      fontWeight:
        'bold',
      fontSize: 12,
    },

    btnSuccess: {
      backgroundColor:
        '#16a34a',
      padding: 15,
      borderRadius: 6,
      alignItems:
        'center',
      marginBottom: 10,
    },

    btnOutline: {
      borderWidth: 1,
      borderColor:
        '#64748b',
      padding: 15,
      borderRadius: 6,
      alignItems:
        'center',
      marginBottom: 10,
    },

    btnOutlineText: {
      color:
        '#475569',
      fontWeight:
        'bold',
    },

    centerContainer: {
      flex: 1,
      justifyContent:
        'center',
      alignItems:
        'center',
      padding: 20,
    },

    processingText: {
      fontSize: 16,
      fontWeight:
        'bold',
      color:
        '#0f172a',
      marginTop: 15,
      textAlign:
        'center',
    },

    processingMuted: {
      fontSize: 12,
      color:
        '#64748b',
      marginTop: 5,
    },

    resultBadgeContainer: {
      backgroundColor:
        '#fef3c7',
      padding: 10,
      borderRadius: 6,
      marginBottom: 15,
      borderWidth: 1,
      borderColor:
        '#fde68a',
    },

    resultBadge: {
      color:
        '#b45309',
      fontSize: 11,
      fontWeight:
        'bold',
      textAlign:
        'center',
    },

    metricsGrid: {
      flexDirection:
        'row',
      gap: 8,
    },

    metricCard: {
      flex: 1,
      backgroundColor:
        '#f8fafc',
      borderWidth: 1,
      borderColor:
        '#e2e8f0',
      borderRadius: 6,
      padding: 10,
      alignItems:
        'center',
    },

    metricValue: {
      fontSize: 16,
      fontWeight:
        'bold',
      color:
        '#0f172a',
    },

    metricLabel: {
      fontSize: 9,
      color:
        '#64748b',
      textTransform:
        'uppercase',
      marginTop: 2,
    },

    confText: {
      fontSize: 12,
      color:
        '#475569',
      marginTop: 15,
      textAlign:
        'center',
    },

    annImage: {
      width:
        '100%',
      height:
        250,
      borderRadius:
        6,
      resizeMode:
        'contain',
    },

    historyRow: {
      flexDirection:
        'row',
      justifyContent:
        'space-between',
      alignItems:
        'center',
      paddingVertical:
        10,
      borderBottomWidth:
        0.5,
      borderBottomColor:
        '#cbd5e1',
    },

    histId: {
      fontWeight:
        'bold',
      color:
        '#0f172a',
      fontSize: 14,
    },

    histSub: {
      fontSize: 10,
      color:
        '#64748b',
    },

    histVol: {
      fontWeight:
        'bold',
      color:
        '#1e3a8a',
      fontSize: 14,
    },

    histStatus: {
      fontSize: 10,
      fontWeight:
        'bold',
    },

    draftItem: {
      padding: 10,
      borderWidth: 1,
      borderColor:
        '#fca5a5',
      backgroundColor:
        '#fee2e2',
      borderRadius: 6,
      marginBottom: 8,
      flexDirection:
        'row',
      alignItems:
        'center',
    },

    draftId: {
      fontWeight:
        'bold',
      color:
        '#991b1b',
      fontSize: 13,
    },

    draftDetails: {
      fontSize: 11,
      color:
        '#7f1d1d',
    },

    draftError: {
      fontSize: 10,
      color:
        '#b91c1c',
      fontStyle:
        'italic',
      marginTop: 2,
    },

    syncBtn: {
      backgroundColor:
        '#b91c1c',
      paddingHorizontal:
        12,
      paddingVertical:
        6,
      borderRadius:
        4,
    },

    syncText: {
      color:
        'white',
      fontSize: 12,
      fontWeight:
        'bold',
    },

    filterBar: {
      flexDirection:
        'row',
      backgroundColor:
        '#e2e8f0',
      padding: 5,
    },

    filterBtn: {
      flex: 1,
      padding: 8,
      alignItems:
        'center',
      borderRadius:
        4,
    },

    filterBtnActive: {
      backgroundColor:
        'white',
    },

    filterText: {
      fontSize: 12,
      fontWeight:
        'bold',
      color:
        '#64748b',
    },

    filterTextActive: {
      color:
        '#0f172a',
    },

    historyCard: {
      backgroundColor:
        'white',
      borderWidth: 1,
      borderColor:
        '#e2e8f0',
      borderRadius: 8,
      padding: 12,
      marginBottom: 10,
    },

    histDetails: {
      fontSize: 11,
      color:
        '#475569',
      marginTop: 2,
    },

    rowBetween: {
      flexDirection:
        'row',
      justifyContent:
        'space-between',
      alignItems:
        'center',
    },

    sectionTitle: {
      fontSize: 20,
      fontWeight:
        'bold',
      color:
        '#0f172a',
      marginBottom: 15,
    },

    linkText: {
      color:
        '#2563eb',
      fontSize: 12,
      fontWeight:
        'bold',
    },

    // =======================================================
    // AR styles
    // =======================================================

    arOverlay: {
      ...StyleSheet.absoluteFillObject,
      justifyContent:
        'space-between',
      paddingTop:
        40,
      paddingLeft:
        15,
      paddingRight:
        15,
      paddingBottom:
        20,
    },

    // -------------------------------------------------------
    // iPhone Measure AR Styles
    // -------------------------------------------------------

    arContainer: {
      flex: 1,
      backgroundColor: '#000000',
    },
    arView: {
      ...StyleSheet.absoluteFillObject,
    },
    reticleContainer: {
      ...StyleSheet.absoluteFillObject,
      justifyContent: 'center',
      alignItems: 'center',
      zIndex: 10,
    },
    reticleRing: {
      // Sized to match Apple Measure's search reticle proportions (roughly a third of a
      // portrait phone's width) rather than a small crosshair.
      width: 120,
      height: 120,
      borderRadius: 60,
      borderWidth: 2.5,
      // Centres the aiming dot below. Without these the dot renders at the
      // ring's top-left corner instead of the middle.
      justifyContent: 'center',
      alignItems: 'center',
    },
    // Centre aiming dot. Marks the exact pixel the native raycast samples, which
    // is the screen centre - the ring alone shows a 120px target area, not the
    // point that will actually be placed.
    reticleDot: {
      width: 10,
      height: 10,
      borderRadius: 5,
    },
    floatingDistanceBadge: {
      position: 'absolute',
      backgroundColor: '#FFFFFF',
      borderRadius: 14,
      paddingHorizontal: 10,
      paddingVertical: 4,
      shadowColor: '#000000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.3,
      shadowRadius: 4,
      elevation: 5,
      zIndex: 15,
    },
    floatingDistanceText: {
      color: '#000000',
      fontWeight: '700',
      fontSize: 13,
      letterSpacing: -0.2,
    },
    arTopOverlay: {
      position: 'absolute',
      top: 48,
      left: 0,
      right: 0,
      alignItems: 'center',
      zIndex: 20,
      paddingHorizontal: 16,
    },
    // Progress pips: how many of the four points native currently holds.
    arProgressRow: {
      flexDirection: 'row',
      gap: 6,
      marginTop: 10,
    },
    arProgressPip: {
      width: 8,
      height: 8,
      borderRadius: 4,
      backgroundColor: 'rgba(255, 255, 255, 0.28)',
      borderWidth: 0.5,
      borderColor: 'rgba(255, 255, 255, 0.5)',
    },
    arProgressPipFilled: {
      backgroundColor: '#FFFFFF',
    },
    // Amber, not red: an advisory describes the shape that was measured, it does not reject it.
    arAdvisoryPill: {
      marginTop: 8,
      alignSelf: 'center',
      maxWidth: '92%',
      backgroundColor: 'rgba(180, 83, 9, 0.92)',
      paddingHorizontal: 14,
      paddingVertical: 9,
      borderRadius: 18,
    },
    arAdvisoryText: {
      color: '#FFFFFF',
      fontSize: 12,
      fontWeight: '600',
      textAlign: 'center',
    },
    diagToggleBtn: {
      backgroundColor: 'rgba(0, 0, 0, 0.6)',
      paddingHorizontal: 10,
      paddingVertical: 6,
      borderRadius: 16,
      borderWidth: 1,
      borderColor: '#334155',
    },
    diagToggleBtnOn: {
      backgroundColor: '#10b981',
      borderColor: '#10b981',
    },
    diagToggleText: {
      color: '#FFFFFF',
      fontSize: 11,
      fontWeight: '700',
    },
    arCircleBtnDisabled: {
      opacity: 0.35,
    },
    // Keeps the undo/back buttons on their same edges once the "+" button is gone.
    arPlusBtnPlaceholder: {
      width: 68,
      height: 68,
    },
    arFooterRow: {
      alignItems: 'center',
      marginTop: 2,
    },
    arClearBtn: {
      paddingHorizontal: 16,
      paddingVertical: 7,
      borderRadius: 16,
      backgroundColor: 'rgba(35, 35, 38, 0.8)',
      borderWidth: 0.5,
      borderColor: 'rgba(255, 255, 255, 0.25)',
    },
    arClearBtnText: {
      color: 'rgba(255, 255, 255, 0.85)',
      fontSize: 13,
      fontWeight: '600',
    },
    arStatusLine: {
      marginTop: 10,
      color: 'rgba(255, 255, 255, 0.6)',
      fontSize: 11,
      textAlign: 'center',
      paddingHorizontal: 8,
    },
    arProblemPill: {
      marginTop: 8,
      alignSelf: 'center',
      maxWidth: '92%',
      backgroundColor: 'rgba(220, 38, 38, 0.92)',
      paddingHorizontal: 14,
      paddingVertical: 9,
      borderRadius: 18,
    },
    arProblemText: {
      color: '#FFFFFF',
      fontSize: 13,
      fontWeight: '700',
      textAlign: 'center',
    },
    topStatusPill: {
      // Shrinks rather than pushing the DIAG toggle off the edge on a narrow screen.
      flexShrink: 1,
      backgroundColor: 'rgba(28, 28, 30, 0.75)',
      paddingHorizontal: 16,
      paddingVertical: 8,
      borderRadius: 20,
      borderWidth: 0.5,
      borderColor: 'rgba(255, 255, 255, 0.2)',
    },
    topStatusText: {
      color: '#FFFFFF',
      fontSize: 14,
      fontWeight: '600',
      textAlign: 'center',
    },
    arBottomBar: {
      position: 'absolute',
      bottom: 24,
      left: 0,
      right: 0,
      alignItems: 'center',
      zIndex: 20,
      paddingHorizontal: 20,
    },
    arBottomRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      width: '100%',
      marginBottom: 16,
    },
    arCircleBtn: {
      width: 50,
      height: 50,
      borderRadius: 25,
      backgroundColor: 'rgba(35, 35, 38, 0.8)',
      justifyContent: 'center',
      alignItems: 'center',
      borderWidth: 0.5,
      borderColor: 'rgba(255, 255, 255, 0.25)',
    },
    arCircleBtnIcon: {
      color: '#FFFFFF',
      fontSize: 22,
      fontWeight: '600',
    },
    arPlusBtn: {
      width: 68,
      height: 68,
      borderRadius: 34,
      borderWidth: 3.5,
      borderColor: '#FFFFFF',
      justifyContent: 'center',
      alignItems: 'center',
    },
    arPlusIconInner: {
      width: 54,
      height: 54,
      borderRadius: 27,
      justifyContent: 'center',
      alignItems: 'center',
    },
    arPlusText: {
      color: '#FFFFFF',
      fontSize: 38,
      fontWeight: '300',
      marginTop: -2,
    },
    validatedSummaryCard: {
      width: '100%',
      backgroundColor: 'rgba(28, 28, 30, 0.92)',
      borderRadius: 16,
      padding: 16,
      marginBottom: 16,
      borderWidth: 0.5,
      borderColor: 'rgba(255, 255, 255, 0.2)',
    },
    validatedHeader: {
      alignItems: 'center',
      marginBottom: 12,
    },
    validatedBadge: {
      color: '#22c55e',
      fontSize: 16,
      fontWeight: '700',
    },
    validatedGrid: {
      flexDirection: 'row',
      justifyContent: 'space-around',
      marginBottom: 14,
    },
    valCell: {
      alignItems: 'center',
    },
    valLabel: {
      color: 'rgba(255, 255, 255, 0.6)',
      fontSize: 11,
      textTransform: 'uppercase',
      marginBottom: 2,
    },
    valValue: {
      color: '#FFFFFF',
      fontSize: 14,
      fontWeight: '600',
    },
    valValueHighlight: {
      color: '#38bdf8',
      fontSize: 15,
      fontWeight: '700',
    },
    analyzeCtaBtn: {
      backgroundColor: '#16a34a',
      borderRadius: 12,
      paddingVertical: 12,
      alignItems: 'center',
    },
    analyzeCtaText: {
      color: '#FFFFFF',
      fontSize: 15,
      fontWeight: '700',
      letterSpacing: 0.5,
    },
    diagContainer: {
      position: 'absolute',
      top: 100,
      left: 12,
      right: 12,
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderRadius: 10,
      padding: 10,
      borderWidth: 1,
      borderColor: '#10b981',
      zIndex: 100,
    },
    diagTitle: {
      color: '#10b981',
      fontSize: 11,
      fontWeight: '700',
      marginBottom: 4,
    },
    diagLine: {
      color: '#cbd5e1',
      fontSize: 10,
      fontFamily: 'monospace',
      lineHeight: 14,
    },
  });