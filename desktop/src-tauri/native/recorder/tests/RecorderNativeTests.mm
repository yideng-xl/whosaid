#include "../RecorderBridge.h"
#include <cassert>
#include <cstring>

int main() {
    assert(whosaid_recorder_api_version() == 1);
    char *json = whosaid_recorder_permission_snapshot();
    assert(json != nullptr);
    assert(std::strstr(json, "systemAudio") != nullptr);
    assert(std::strstr(json, "microphone") != nullptr);
    whosaid_recorder_free_string(json);
    return 0;
}
