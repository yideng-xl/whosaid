#include "../RecorderBridge.h"
#include "../RecorderPermissionState.h"
#include "../TimelineWriter.h"

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

        assert(WSSilenceFrames(1'000'000'000, 1'250'000'000, 48'000) == 12'000);
        assert(WSSilenceFrames(1'000'000'000, 999'000'000, 48'000) == 0);

        NSURL *timelineURL = [[NSURL fileURLWithPath:NSTemporaryDirectory()]
            URLByAppendingPathComponent:[NSString stringWithFormat:@"whosaid-%@.caf",
                                                                    NSUUID.UUID.UUIDString]];
        NSError *timelineError = nil;
        WSTimelineWriter *writer = [[WSTimelineWriter alloc] initWithURL:timelineURL
                                                              sampleRate:48'000
                                                            sessionStart:1'000'000'000
                                                                   error:&timelineError];
        assert(writer != nil);
        assert(timelineError == nil);
        AVAudioFormat *format = [[AVAudioFormat alloc] initStandardFormatWithSampleRate:48'000
                                                                               channels:1];
        AVAudioPCMBuffer *buffer = [[AVAudioPCMBuffer alloc] initWithPCMFormat:format
                                                                frameCapacity:4'800];
        buffer.frameLength = 4'800;
        assert([writer appendBuffer:buffer receivedAt:1'250'000'000 error:&timelineError]);
        assert([writer appendBuffer:buffer receivedAt:1'349'000'000 error:&timelineError]);
        assert([writer close:&timelineError]);
        AVAudioFile *timeline = [[AVAudioFile alloc] initForReading:timelineURL
                                                              error:&timelineError];
        assert(timeline != nil);
        assert(timeline.length == 21'600);
        [[NSFileManager defaultManager] removeItemAtURL:timelineURL error:nil];

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
