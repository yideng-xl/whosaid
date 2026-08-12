#include "../RecorderBridge.h"
#include "../RecorderPermissionState.h"

#import <Foundation/Foundation.h>

#include <cassert>
#include <cstring>

int main() {
    @autoreleasepool {
        assert(whosaid_recorder_api_version() == 1);

        assert(std::strcmp(whosaid_system_audio_permission_state(true, false), "granted") == 0);
        assert(std::strcmp(whosaid_system_audio_permission_state(true, true), "granted") == 0);
        assert(std::strcmp(whosaid_system_audio_permission_state(false, false),
                           "notDetermined") == 0);
        assert(std::strcmp(whosaid_system_audio_permission_state(false, true), "denied") == 0);

        char *json = whosaid_recorder_permission_snapshot();
        assert(json != nullptr);

        NSData *data = [NSData dataWithBytes:json length:std::strlen(json)];
        NSError *error = nil;
        id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
        assert(error == nil);
        assert([value isKindOfClass:[NSDictionary class]]);

        NSDictionary *snapshot = (NSDictionary *)value;
        assert(snapshot.count == 2);
        NSSet *allowed = [NSSet setWithObjects:@"granted", @"denied", @"notDetermined", nil];
        assert([allowed containsObject:snapshot[@"systemAudio"]]);
        assert([allowed containsObject:snapshot[@"microphone"]]);

        whosaid_recorder_free_string(json);
    }
    return 0;
}
