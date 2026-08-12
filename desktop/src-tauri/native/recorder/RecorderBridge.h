#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*WhosaidRecorderCallback)(const char *json, void *context);
int32_t whosaid_recorder_api_version(void);
char *whosaid_recorder_permission_snapshot(void);
void whosaid_recorder_open_settings(int32_t pane); /* 1=系统录音, 2=麦克风 */
int32_t whosaid_recorder_start(const char *session_dir,
                               WhosaidRecorderCallback callback,
                               void *context);
int32_t whosaid_recorder_stop(void);
void whosaid_recorder_free_string(char *value);

#ifdef __cplusplus
}
#endif
