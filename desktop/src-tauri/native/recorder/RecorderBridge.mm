#import "RecorderBridge.h"
#import "RecorderPermissionState.h"

#import <AppKit/AppKit.h>
#import <AVFoundation/AVFoundation.h>
#import <CoreGraphics/CGWindow.h>

#include <cstdio>
#include <cstdlib>

namespace {

NSString *const system_audio_permission_requested_key =
    @"com.yideng.whosaid.recorder.systemAudioPermissionRequested";

const char *microphone_permission() {
    switch ([AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio]) {
    case AVAuthorizationStatusAuthorized:
        return "granted";
    case AVAuthorizationStatusNotDetermined:
        return "notDetermined";
    case AVAuthorizationStatusDenied:
    case AVAuthorizationStatusRestricted:
        return "denied";
    }

    return "denied";
}

const char *system_audio_permission() {
    const bool granted = CGPreflightScreenCaptureAccess();
    const bool requested = [[NSUserDefaults standardUserDefaults]
        boolForKey:system_audio_permission_requested_key];
    return whosaid_system_audio_permission_state(granted, requested);
}

} // namespace

int32_t whosaid_recorder_api_version(void) {
    return 1;
}

char *whosaid_recorder_permission_snapshot(void) {
    const char *system_audio = system_audio_permission();
    const char *microphone = microphone_permission();
    const int size = std::snprintf(nullptr, 0,
                                   "{\"systemAudio\":\"%s\",\"microphone\":\"%s\"}",
                                   system_audio, microphone);
    if (size < 0) {
        return nullptr;
    }

    char *snapshot = static_cast<char *>(std::malloc(static_cast<size_t>(size) + 1));
    if (snapshot == nullptr) {
        return nullptr;
    }

    std::snprintf(snapshot, static_cast<size_t>(size) + 1,
                  "{\"systemAudio\":\"%s\",\"microphone\":\"%s\"}",
                  system_audio, microphone);
    return snapshot;
}

int32_t whosaid_recorder_request_system_audio_permission(void) {
    const bool granted = CGRequestScreenCaptureAccess();
    [[NSUserDefaults standardUserDefaults] setBool:YES
                                           forKey:system_audio_permission_requested_key];
    return granted ? 1 : 0;
}

void whosaid_recorder_open_settings(int32_t pane) {
    NSString *url_string = nil;
    if (pane == 1) {
        url_string = @"x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture";
    } else if (pane == 2) {
        url_string = @"x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone";
    }

    if (url_string != nil) {
        [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:url_string]];
    }
}

int32_t whosaid_recorder_start(const char *session_dir,
                               WhosaidRecorderCallback callback,
                               void *context) {
    (void)session_dir;
    (void)callback;
    (void)context;
    return -1;
}

int32_t whosaid_recorder_stop(void) {
    return -1;
}

void whosaid_recorder_free_string(char *value) {
    std::free(value);
}
