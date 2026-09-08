#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*WhosaidRecorderCallback)(const char *json, void *context);
typedef void (*WhosaidPowerCallback)(int32_t event, void *context);

enum WhosaidPowerEvent {
    WhosaidPowerEventDisplaySleep = 1,
    WhosaidPowerEventDisplayWake = 2,
    WhosaidPowerEventSystemSleep = 3,
    WhosaidPowerEventSystemWake = 4,
};
int32_t whosaid_recorder_api_version(void);
char *whosaid_recorder_permission_snapshot(void);
int32_t whosaid_recorder_request_system_audio_permission(void); /* 1=已授权, 0=未授权 */
void whosaid_recorder_open_settings(int32_t pane); /* 1=系统录音, 2=麦克风 */
int32_t whosaid_recorder_start(const char *session_dir,
                               WhosaidRecorderCallback callback,
                               void *context);
int32_t whosaid_recorder_stop(void);
int32_t whosaid_recorder_watch_power_events(WhosaidPowerCallback callback,
                                            void *context);
void whosaid_recorder_free_string(char *value);

#ifdef __cplusplus
}
#endif
