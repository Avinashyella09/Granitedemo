Pod::Spec.new do |s|
  s.name           = 'GraniteAR'
  s.version        = '1.0.0'
  s.summary        = 'Native iOS AR measurement module for the Granite Blocks Field Officer App.'
  s.description    = 'LiDAR-aware ARKit + RealityKit 4-point block measurement behind the shared GraniteAR Expo module contract (see the Android ARCore implementation in ../android for the JS-facing behavior this mirrors).'
  s.license        = 'MIT'
  s.author         = 'Granite Blocks'
  s.homepage       = 'https://github.com/Avinashyella09/Granitedemo'
  s.platforms      = { :ios => '15.1' }
  s.source         = { git: 'https://github.com/Avinashyella09/Granitedemo.git' }
  s.static_framework = true

  s.dependency 'ExpoModulesCore'

  s.pod_target_xcconfig = {
    'DEFINES_MODULE' => 'YES',
    'SWIFT_COMPILATION_MODE' => 'wholemodule'
  }

  s.source_files = "**/*.{h,m,swift}"
end
