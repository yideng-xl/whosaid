fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        cc::Build::new()
            .cpp(true)
            .file("native/recorder/RecorderBridge.mm")
            .file("native/recorder/TimelineWriter.mm")
            .flag("-fobjc-arc")
            .flag("-std=c++17")
            .flag("-fblocks")
            .compile("whosaid_recorder");

        println!("cargo:rerun-if-changed=native/recorder/RecorderBridge.h");
        println!("cargo:rerun-if-changed=native/recorder/AudioMeter.h");
        println!("cargo:rerun-if-changed=native/recorder/RecorderPermissionState.h");
        println!("cargo:rerun-if-changed=native/recorder/RecorderSessionGate.h");
        println!("cargo:rerun-if-changed=native/recorder/RecorderBridge.mm");
        println!("cargo:rerun-if-changed=native/recorder/TimelineWriter.h");
        println!("cargo:rerun-if-changed=native/recorder/TimelineWriter.mm");
        println!("cargo:rustc-link-lib=framework=ScreenCaptureKit");
        println!("cargo:rustc-link-lib=framework=AVFoundation");
        println!("cargo:rustc-link-lib=framework=CoreMedia");
        println!("cargo:rustc-link-lib=framework=CoreAudio");
        println!("cargo:rustc-link-lib=framework=AppKit");
        println!("cargo:rustc-link-lib=framework=Foundation");
    }

    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        cc::Build::new()
            .cpp(true)
            .file("native/recorder/RecorderBridgeWindows.cpp")
            .flag("/std:c++17")
            .flag("/EHsc")
            .flag("/utf-8")
            .compile("whosaid_recorder");
        for file in ["RecorderBridgeWindows.cpp", "RecorderBridge.h", "WindowsTimeline.h"] {
            println!("cargo:rerun-if-changed=native/recorder/{file}");
        }
        for library in ["ole32", "uuid", "user32", "shell32"] {
            println!("cargo:rustc-link-lib={library}");
        }
    }
    tauri_build::build()
}
