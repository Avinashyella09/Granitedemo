import React, { useState, useEffect } from 'react';
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
  Modal,
  Animated
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

  const [arPoints, setArPoints] =
    useState([]);

  const [arDistance, setArDistance] =
    useState(null);

  // AR derived measurements (populated when P4 is tapped)
  const [arLength, setArLength]   = useState(null);
  const [arBreadth, setArBreadth] = useState(null);
  const [arHeight, setArHeight]   = useState(null);
  const [arVolume, setArVolume]   = useState(null);

  const [tapToken, setTapToken] = useState(0);
  const [resetToken, setResetToken] = useState(0);
  const [arOverlayLabels, setArOverlayLabels] = useState([]);
  const [candidateValid, setCandidateValid] = useState(false);
  const [trackingQuality, setTrackingQuality] = useState('GOOD');
  const [arDiagnostics, setArDiagnostics] = useState(null);
  const [showDiag, setShowDiag] = useState(false);

  // =========================================================
  // AR steadiness (accelerometer motion gate)
  //
  // The device has no LiDAR, so measurement quality rests on ARCore keeping
  // visual-inertial tracking converged. The native view watches the
  // accelerometer and reports STEADY / MOVING; while MOVING the measurement is
  // halted and a blocking popup asks the officer to hold still. Points already
  // locked are preserved, so a bump costs a pause and not the whole scan.
  // =========================================================

  const [arSteady, setArSteady] = useState(true);
  const [arMotionMagnitude, setArMotionMagnitude] = useState(0);
  const [arMotionThreshold, setArMotionThreshold] = useState(0.55);
  const [arMotionSensorAvailable, setArMotionSensorAvailable] = useState(true);
  const [showSteadyPopup, setShowSteadyPopup] = useState(false);

  // Pulsing ring behind the popup icon, driven while the popup is visible.
  const steadyPulse = React.useRef(new Animated.Value(0)).current;

  // Latest point count, readable from the native event callback without
  // re-subscribing the native view on every point placement.
  const arPointsCountRef = React.useRef(0);

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
  // AR point selection
  // =========================================================

  const handleARPointSelected = (event) => {
    const point = event.nativeEvent;
    console.log('AR POINT:', point);

    setArPoints(previous => {
      if (previous.length >= 4) {
        return previous;
      }

      const dist3d = (a, b) => Math.sqrt(
        (b.x - a.x) ** 2 + (b.y - a.y) ** 2 + (b.z - a.z) ** 2
      );

      // Validate distance to previous point (must be >= 5cm)
      if (previous.length > 0) {
        const lastP = previous[previous.length - 1];
        const stepDist = dist3d(lastP, point);
        if (stepDist < 0.05) {
          Alert.alert(
            'Point Unstable / Too Close',
            'Selected point is too close to the previous point (< 5 cm). Please select a distinct corner.'
          );
          return previous;
        }
      }

      const newPoint = {
        x: point.x,
        y: point.y,
        z: point.z,
        trackable: point.trackable || 'Plane'
      };

      const nextPoints = [...previous, newPoint];

      if (nextPoints.length === 1) {
        setArDistance(null);
        setArStatus('P1 set (Bottom-Front-Left) — aim at P2 (Bottom-Front-Right corner)');

      } else if (nextPoints.length === 2) {
        const l = dist3d(nextPoints[0], nextPoints[1]);
        setArDistance(l);
        setArStatus(`Length: ${l.toFixed(2)} m — aim at P3 (Top-Front-Right corner for Height)`);

      } else if (nextPoints.length === 3) {
        const h = dist3d(nextPoints[1], nextPoints[2]);
        setArStatus(`Height: ${h.toFixed(2)} m — aim at P4 (Top-Back-Right corner for Depth/Breadth)`);

      } else if (nextPoints.length === 4) {
        // Canonical Definition:
        // P1→P2 = Length (L)
        // P2→P3 = Height (H)
        // P3→P4 = Breadth/Depth (B)
        const p1 = nextPoints[0], p2 = nextPoints[1], p3 = nextPoints[2], p4 = nextPoints[3];
        
        const l = dist3d(p1, p2);
        const h = dist3d(p2, p3);
        const b = dist3d(p3, p4);

        // Vector direction analysis
        const v1 = { x: p2.x - p1.x, y: p2.y - p1.y, z: p2.z - p1.z };
        const v2 = { x: p3.x - p2.x, y: p3.y - p2.y, z: p3.z - p2.z };
        const v3 = { x: p4.x - p3.x, y: p4.y - p3.y, z: p4.z - p3.z };

        // Dot products for orthogonality
        const dot12 = (v1.x * v2.x + v1.y * v2.y + v1.z * v2.z) / (l * h);
        const dot23 = (v2.x * v3.x + v2.y * v3.y + v2.z * v3.z) / (h * b);

        const v = parseFloat((l * b * h).toFixed(4));
        const maxFrontDim = Math.max(l, h);

        // Geometry & volume sanity checks
        const isSanelyBounded = (l >= 0.1 && l <= 10.0) && (h >= 0.1 && h <= 5.0) && (b >= 0.1 && b <= 5.0);
        const isDepthPlausible = b <= 2.5 * maxFrontDim && v <= 25.0;
        const isOrthogonalEnough = Math.abs(dot12) < 0.75 && Math.abs(dot23) < 0.75;

        if (!isSanelyBounded || !isDepthPlausible || !isOrthogonalEnough) {
          Alert.alert(
            'Validation Failed',
            'Measurement could not be validated. Geometrical vectors are inconsistent or P4 is outside expected block bounds. Please rescan the block.'
          );
          setArStatus('❌ Measurement could not be validated. Please rescan the block.');
          return previous;
        }

        setArLength(parseFloat(l.toFixed(4)));
        setArBreadth(parseFloat(b.toFixed(4)));
        setArHeight(parseFloat(h.toFixed(4)));
        setArVolume(v);

        setArStatus(
          `✅ Validated: L=${l.toFixed(2)}m  B=${b.toFixed(2)}m  H=${h.toFixed(2)}m  V=${v}m³`
        );
      }

      return nextPoints;
    });
  };

  // =========================================================
  // Reset AR
  // =========================================================

  const resetAR = () => {
    setArPoints([]);
    setArDistance(null);
    setArStatus('Starting AR...');
    setArLength(null);
    setArBreadth(null);
    setArHeight(null);
    setArVolume(null);
    setArOverlayLabels([]);
    setCandidateValid(false);
    setTrackingQuality('GOOD');
    setShowSteadyPopup(false);
    setArMotionMagnitude(0);
    setTapToken(0);
    setResetToken((t) => t + 1);
  };

  const handleAROverlayUpdate = (event) => {
    const data = event.nativeEvent;
    if (!data) return;

    if (data.labels) {
      setArOverlayLabels(data.labels);
    }
    if (data.candidateValid !== undefined) {
      setCandidateValid(data.candidateValid);
    }
    if (data.trackingQuality) {
      setTrackingQuality(data.trackingQuality);
    }
    if (data.diagnostics) {
      setArDiagnostics(data.diagnostics);
    }
    if (typeof data.motionMagnitude === 'number') {
      setArMotionMagnitude(data.motionMagnitude);
    }
    if (typeof data.movingThreshold === 'number') {
      setArMotionThreshold(data.movingThreshold);
    }
    if (typeof data.motionSensorAvailable === 'boolean') {
      setArMotionSensorAvailable(data.motionSensorAvailable);
    }
  };

  // =========================================================
  // Accelerometer steadiness transitions
  // =========================================================

  const handleARSteadyChange = (event) => {
    const data = event.nativeEvent;
    if (!data) return;

    const steady = !!data.steady;
    setArSteady(steady);

    if (typeof data.magnitude === 'number') {
      setArMotionMagnitude(data.magnitude);
    }
    if (typeof data.sensorAvailable === 'boolean') {
      setArMotionSensorAvailable(data.sensorAvailable);
    }

    console.log(
      `AR MOTION: ${data.state} mag=${Number(data.magnitude || 0).toFixed(3)} ` +
      `peak=${Number(data.peakMagnitude || 0).toFixed(3)} points=${data.pointsPlaced}`
    );

    if (!steady) {
      // Only interrupt while a measurement is actually in progress. Once all
      // four corners are locked the numbers are frozen, so movement is
      // harmless and a popup would just be in the way.
      if (arPointsCountRef.current < 4) {
        setShowSteadyPopup(true);
      }
    } else {
      setShowSteadyPopup(false);
    }
  };

  // Keep the ref in sync so the native callback can read the live count.
  useEffect(() => {
    arPointsCountRef.current = arPoints.length;

    // Movement after the final point no longer matters.
    if (arPoints.length >= 4) {
      setShowSteadyPopup(false);
    }
  }, [arPoints.length]);

  // Leaving the AR screen must never strand the popup on top of another view.
  useEffect(() => {
    if (screen !== 'ar-measure') {
      setShowSteadyPopup(false);
    }
  }, [screen]);

  // Pulse the popup icon ring while the phone is being held still.
  useEffect(() => {
    if (!showSteadyPopup) {
      steadyPulse.setValue(0);
      return;
    }

    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(steadyPulse, {
          toValue: 1,
          duration: 900,
          useNativeDriver: true
        }),
        Animated.timing(steadyPulse, {
          toValue: 0,
          duration: 900,
          useNativeDriver: true
        })
      ])
    );

    loop.start();
    return () => loop.stop();
  }, [showSteadyPopup, steadyPulse]);

  const getARInstruction = () => {
    if (!arSteady) {
      return 'Hold still — measurement paused';
    }
    if (trackingQuality === 'INSUFFICIENT') {
      return 'Move phone slowly to scan the block';
    }
    switch (arPoints.length) {
      case 0:
        return 'Select P1 (Bottom-Front-Left corner)';
      case 1:
        return 'Select P2 (Bottom-Front-Right corner)';
      case 2:
        return 'Select P3 (Top-Front-Right corner for Height)';
      case 3:
        return 'Select P4 (Top-Back-Right corner for Depth)';
      case 4:
        return 'Measurement Validated';
      default:
        return 'Move phone slowly to scan the block';
    }
  };

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
      Alert.alert('Error', 'Complete the AR measurement (select all 4 points) first.');
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
        ar_points:     arPoints,
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
          {/* Native ARCore view */}
          <GraniteARView
            style={styles.arView}
            tapToken={tapToken}
            resetToken={resetToken}
            onStatus={(event) => {
              const status = event.nativeEvent?.status || 'UNKNOWN';
              const message = event.nativeEvent?.message;
              setArStatus(message ? `${status}: ${message}` : status);
            }}
            onPointSelected={handleARPointSelected}
            onOverlayUpdate={handleAROverlayUpdate}
            onSteadyChange={handleARSteadyChange}
          />

          {/* Center Target Reticle (when points < 4) */}
          {arPoints.length < 4 && (
            <View style={styles.reticleContainer} pointerEvents="none">
              <View
                style={[
                  styles.reticleRing,
                  {
                    borderColor: !arSteady
                      ? '#f59e0b'
                      : candidateValid
                        ? '#FFFFFF'
                        : 'rgba(255,255,255,0.4)'
                  }
                ]}
              >
                <View
                  style={[
                    styles.reticleDot,
                    {
                      backgroundColor: !arSteady
                        ? '#f59e0b'
                        : candidateValid
                          ? '#FFFFFF'
                          : 'rgba(255,255,255,0.4)'
                    }
                  ]}
                />
              </View>
            </View>
          )}

          {/* Floating Edge Distance Badges (iPhone Measure style) */}
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

          {/* Top Status Pill Overlay + Diagnostic Toggle */}
          <View style={styles.arTopOverlay} pointerEvents="box-none">
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <View style={styles.topStatusPill}>
                <Text style={styles.topStatusText}>{getARInstruction()}</Text>
              </View>
              <TouchableOpacity
                style={{ backgroundColor: showDiag ? '#10b981' : 'rgba(0,0,0,0.6)', paddingHorizontal: 10, paddingVertical: 6, borderRadius: 16, borderHeight: 1, borderColor: '#334155' }}
                onPress={() => setShowDiag(prev => !prev)}
              >
                <Text style={{ color: '#FFFFFF', fontSize: 11, fontWeight: '700' }}>DIAG</Text>
              </TouchableOpacity>
            </View>
          </View>

          {/* Development Diagnostic Overlay HUD (Requirement 19) */}
          {showDiag && arDiagnostics && (
            <View style={styles.diagContainer} pointerEvents="none">
              <Text style={styles.diagTitle}>⚙ AR DIAGNOSTIC MODE</Text>
              <Text style={styles.diagLine}>Reticle: ({arDiagnostics.reticleX?.toFixed(0)}, {arDiagnostics.reticleY?.toFixed(0)}) | Viewport: {arDiagnostics.viewportW}x{arDiagnostics.viewportH} | Rot: {arDiagnostics.rotation}</Text>
              <Text style={styles.diagLine}>Tracking: {trackingQuality} | CandValid: {candidateValid ? 'YES' : 'NO'}</Text>
              <Text style={styles.diagLine}>Candidate World: X={arDiagnostics.candidateX?.toFixed(3)} Y={arDiagnostics.candidateY?.toFixed(3)} Z={arDiagnostics.candidateZ?.toFixed(3)}</Text>
              <Text style={styles.diagLine}>P1 Locked: {arDiagnostics.p1 ? `${arDiagnostics.p1[0].toFixed(3)}, ${arDiagnostics.p1[1].toFixed(3)}, ${arDiagnostics.p1[2].toFixed(3)}` : 'NONE'}</Text>
              <Text style={styles.diagLine}>P2 Locked: {arDiagnostics.p2 ? `${arDiagnostics.p2[0].toFixed(3)}, ${arDiagnostics.p2[1].toFixed(3)}, ${arDiagnostics.p2[2].toFixed(3)}` : 'NONE'}</Text>
              <Text style={styles.diagLine}>P3 Locked: {arDiagnostics.p3 ? `${arDiagnostics.p3[0].toFixed(3)}, ${arDiagnostics.p3[1].toFixed(3)}, ${arDiagnostics.p3[2].toFixed(3)}` : 'NONE'}</Text>
              <Text style={styles.diagLine}>P4 Locked: {arDiagnostics.p4 ? `${arDiagnostics.p4[0].toFixed(3)}, ${arDiagnostics.p4[1].toFixed(3)}, ${arDiagnostics.p4[2].toFixed(3)}` : 'NONE'}</Text>
            </View>
          )}

          {/* Bottom Controls Bar (iPhone Measure UI) */}
          <View style={styles.arBottomBar} pointerEvents="box-none">
            {/* Validated Summary Card + Analyze Block CTA (when points === 4) */}
            {arPoints.length === 4 && arLength !== null && (
              <View style={styles.validatedSummaryCard}>
                <View style={styles.validatedHeader}>
                  <Text style={styles.validatedBadge}>✓ Measurement Validated</Text>
                </View>
                <View style={styles.validatedGrid}>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>Length</Text>
                    <Text style={styles.valValue}>{arLength.toFixed(2)} m</Text>
                  </View>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>Height</Text>
                    <Text style={styles.valValue}>{arHeight.toFixed(2)} m</Text>
                  </View>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>Breadth</Text>
                    <Text style={styles.valValue}>{arBreadth.toFixed(2)} m</Text>
                  </View>
                  <View style={styles.valCell}>
                    <Text style={styles.valLabel}>Volume</Text>
                    <Text style={styles.valValueHighlight}>{arVolume} m³</Text>
                  </View>
                </View>
                <TouchableOpacity
                  style={styles.analyzeCtaBtn}
                  onPress={() => setScreen('new-inspection')}
                >
                  <Text style={styles.analyzeCtaText}>✓ USE THESE MEASUREMENTS</Text>
                </TouchableOpacity>
              </View>
            )}

            {/* Bottom Row Controls */}
            <View style={styles.arBottomRow}>
              {/* Reset / Undo Button (Left) */}
              <TouchableOpacity
                style={styles.arCircleBtn}
                onPress={resetAR}
                activeOpacity={0.7}
              >
                <Text style={styles.arCircleBtnIcon}>↺</Text>
              </TouchableOpacity>

              {/* Action "+" Button (Center - when points < 4) */}
              {arPoints.length < 4 && (
                <TouchableOpacity
                  style={[
                    styles.arPlusBtn,
                    {
                      backgroundColor: !arSteady
                        ? 'rgba(245,158,11,0.18)'
                        : candidateValid
                          ? 'rgba(255,255,255,0.3)'
                          : 'rgba(255,255,255,0.1)'
                    }
                  ]}
                  disabled={!arSteady}
                  onPress={() => {
                    // Belt-and-braces: the native side rejects taps while moving
                    // too, but blocking here keeps the popup the single source
                    // of truth about why nothing happened.
                    if (!arSteady) {
                      setShowSteadyPopup(true);
                      return;
                    }
                    setTapToken(prev => prev + 1);
                  }}
                  activeOpacity={0.7}
                >
                  <View style={[styles.arPlusIconInner, !arSteady && { opacity: 0.35 }]}>
                    <Text style={styles.arPlusText}>+</Text>
                  </View>
                </TouchableOpacity>
              )}

              {/* Back Button (Right) */}
              <TouchableOpacity
                style={styles.arCircleBtn}
                onPress={() => setScreen('new-inspection')}
                activeOpacity={0.7}
              >
                <Text style={{ color: '#FFFFFF', fontSize: 18, fontWeight: '600' }}>←</Text>
              </TouchableOpacity>
            </View>

            {/* Bottom Mode Selector */}
            <View style={styles.bottomModeSelector}>
              <View style={styles.modeTabActive}>
                <Text style={styles.modeTabActiveText}>Measure</Text>
              </View>
              <View style={styles.modeTabInactive}>
                <Text style={styles.modeTabInactiveText}>Level</Text>
              </View>
            </View>
          </View>

          {/* =================================================
              HOLD STEADY POPUP

              Shown whenever the accelerometer reports motion during an
              in-progress measurement. It is intentionally not dismissable by
              tapping outside: the measurement is halted until the phone is
              actually still, and the popup clears itself the moment the native
              detector reports STEADY again.
             ================================================= */}
          <Modal
            visible={showSteadyPopup}
            transparent
            animationType="fade"
            statusBarTranslucent
            onRequestClose={() => {}}
          >
            <View style={styles.steadyBackdrop}>
              <View style={styles.steadyCard}>

                <Animated.View
                  style={[
                    styles.steadyPulseRing,
                    {
                      opacity: steadyPulse.interpolate({
                        inputRange: [0, 1],
                        outputRange: [0.25, 0.7]
                      }),
                      transform: [
                        {
                          scale: steadyPulse.interpolate({
                            inputRange: [0, 1],
                            outputRange: [1, 1.18]
                          })
                        }
                      ]
                    }
                  ]}
                />

                <View style={styles.steadyIconCircle}>
                  <Text style={styles.steadyIcon}>✋</Text>
                </View>

                <Text style={styles.steadyTitle}>Hold the Phone Steady</Text>

                <Text style={styles.steadyMessage}>
                  Movement was detected, so the measurement has been paused.
                  Keep the phone still and it will resume automatically.
                </Text>

                {/* Live motion meter fed by the accelerometer */}
                <View style={styles.steadyMeterTrack}>
                  <View
                    style={[
                      styles.steadyMeterFill,
                      {
                        width: `${Math.max(
                          4,
                          Math.min(
                            100,
                            (arMotionMagnitude / Math.max(arMotionThreshold * 2.5, 0.1)) * 100
                          )
                        )}%`,
                        backgroundColor: arMotionMagnitude > arMotionThreshold ? '#ef4444' : '#f59e0b'
                      }
                    ]}
                  />
                </View>

                <Text style={styles.steadyMeterLabel}>
                  Motion: {arMotionMagnitude.toFixed(2)} m/s²  (limit {arMotionThreshold.toFixed(2)})
                </Text>

                {arPoints.length > 0 && (
                  <Text style={styles.steadyProgressNote}>
                    {arPoints.length} of 4 points already locked — they have been kept.
                  </Text>
                )}

                {!arMotionSensorAvailable && (
                  <Text style={styles.steadyWarnNote}>
                    Accelerometer unavailable — steadiness cannot be verified.
                  </Text>
                )}

                <TouchableOpacity
                  style={styles.steadyCancelBtn}
                  onPress={() => {
                    setShowSteadyPopup(false);
                    setScreen('new-inspection');
                  }}
                  activeOpacity={0.8}
                >
                  <Text style={styles.steadyCancelText}>Cancel Measurement</Text>
                </TouchableOpacity>

              </View>
            </View>
          </Modal>
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
                  <Text style={styles.btnSecText}>🔄 RE-MEASURE (AR)</Text>
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
                <Text style={styles.btnSecText}>📐 OPEN AR MEASUREMENT</Text>
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

    arContainer: {
      flex: 1,
      backgroundColor:
        '#000',
    },

    arView: {
      flex: 1,
    },

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
      width: 36,
      height: 36,
      borderRadius: 18,
      borderWidth: 2,
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: 'rgba(0, 0, 0, 0.1)',
    },
    reticleDot: {
      width: 6,
      height: 6,
      borderRadius: 3,
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
    },
    topStatusPill: {
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
    bottomModeSelector: {
      flexDirection: 'row',
      backgroundColor: 'rgba(35, 35, 38, 0.85)',
      borderRadius: 20,
      padding: 3,
      borderWidth: 0.5,
      borderColor: 'rgba(255, 255, 255, 0.2)',
    },
    modeTabActive: {
      backgroundColor: 'rgba(255, 255, 255, 0.25)',
      paddingHorizontal: 18,
      paddingVertical: 5,
      borderRadius: 17,
    },
    modeTabActiveText: {
      color: '#FFFFFF',
      fontSize: 13,
      fontWeight: '600',
    },
    modeTabInactive: {
      paddingHorizontal: 18,
      paddingVertical: 5,
      borderRadius: 17,
    },
    modeTabInactiveText: {
      color: 'rgba(255, 255, 255, 0.5)',
      fontSize: 13,
      fontWeight: '500',
    },

    // =======================================================
    // Hold-steady popup (accelerometer motion gate)
    // =======================================================
    steadyBackdrop: {
      flex: 1,
      backgroundColor: 'rgba(0, 0, 0, 0.78)',
      justifyContent: 'center',
      alignItems: 'center',
      paddingHorizontal: 28,
    },
    steadyCard: {
      width: '100%',
      maxWidth: 380,
      backgroundColor: '#111827',
      borderRadius: 22,
      paddingVertical: 26,
      paddingHorizontal: 22,
      alignItems: 'center',
      borderWidth: 1,
      borderColor: 'rgba(245, 158, 11, 0.45)',
    },
    steadyPulseRing: {
      position: 'absolute',
      top: 18,
      width: 88,
      height: 88,
      borderRadius: 44,
      backgroundColor: 'rgba(245, 158, 11, 0.28)',
    },
    steadyIconCircle: {
      width: 68,
      height: 68,
      borderRadius: 34,
      backgroundColor: 'rgba(245, 158, 11, 0.18)',
      borderWidth: 2,
      borderColor: '#f59e0b',
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 16,
    },
    steadyIcon: {
      fontSize: 32,
    },
    steadyTitle: {
      color: '#FFFFFF',
      fontSize: 20,
      fontWeight: '700',
      textAlign: 'center',
      marginBottom: 10,
    },
    steadyMessage: {
      color: '#cbd5e1',
      fontSize: 14,
      lineHeight: 20,
      textAlign: 'center',
      marginBottom: 18,
    },
    steadyMeterTrack: {
      width: '100%',
      height: 8,
      borderRadius: 4,
      backgroundColor: 'rgba(255, 255, 255, 0.12)',
      overflow: 'hidden',
      marginBottom: 8,
    },
    steadyMeterFill: {
      height: '100%',
      borderRadius: 4,
    },
    steadyMeterLabel: {
      color: '#94a3b8',
      fontSize: 12,
      fontVariant: ['tabular-nums'],
      marginBottom: 12,
    },
    steadyProgressNote: {
      color: '#22c55e',
      fontSize: 12,
      fontWeight: '600',
      textAlign: 'center',
      marginBottom: 14,
    },
    steadyWarnNote: {
      color: '#f87171',
      fontSize: 12,
      textAlign: 'center',
      marginBottom: 14,
    },
    steadyCancelBtn: {
      paddingVertical: 10,
      paddingHorizontal: 20,
      borderRadius: 12,
      borderWidth: 1,
      borderColor: 'rgba(255, 255, 255, 0.25)',
    },
    steadyCancelText: {
      color: '#e2e8f0',
      fontSize: 13,
      fontWeight: '600',
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