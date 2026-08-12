#pragma once

static inline const char *whosaid_system_audio_permission_state(bool granted,
                                                                bool requested) {
    if (granted) {
        return "granted";
    }
    return requested ? "denied" : "notDetermined";
}
