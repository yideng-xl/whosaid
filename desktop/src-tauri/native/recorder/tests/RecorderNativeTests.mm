#include "../RecorderBridge.h"
#include "../RecorderPermissionState.h"
#include "../RecorderSessionGate.h"
#include "../TimelineWriter.h"

#import <Foundation/Foundation.h>

#include <cassert>
#include <atomic>
#include <cmath>
#include <cstring>
#include <memory>

enum WSMicStartResult {
    WSMicStartResultDenied,
    WSMicStartResultUnavailable,
    WSMicStartResultInterrupted,
};

bool WSMicFailureIsFatal(WSMicStartResult result);
bool WSMicrophoneFailureAllowsReconnect(bool writerCreationAttempted,
                                        bool writerAppendAttempted);
AVAudioPCMBuffer *WSConvertMicrophoneBuffer(AVAudioPCMBuffer *buffer, NSError **error);
NSDictionary<NSString *, id> *WSSessionManifest(NSString *sessionID,
                                                 NSNumber *startedAt,
                                                 BOOL microphoneHasFrames,
                                                 NSString *systemStatus,
                                                 NSString *microphoneStatus);
NSArray<NSDictionary<NSString *, id> *> *WSSourceStatusEventsAfterPersistence(
    BOOL persisted,
    NSString *source,
    NSString *status,
    NSError *error);
NSArray<NSDictionary<NSString *, id> *> *WSStopTerminalEventsAfterPersistence(
    BOOL persisted,
    NSString *sessionDirectory,
    NSString *systemTrack,
    NSString *microphoneTrack,
    NSError *error);

@interface WSRecorderTerminalController : NSObject
- (BOOL)allowsSuccess;
- (void)failWithMessage:(NSString *)message
                prepare:(dispatch_block_t)prepare
             stopSystem:(void (^)(void (^completion)(NSError *error)))stopSystem
                cleanup:(dispatch_block_t)cleanup
                   emit:(void (^)(NSDictionary<NSString *, id> *event))emit
                release:(dispatch_block_t)release;
- (BOOL)emitStoppedEvent:(NSDictionary<NSString *, id> *)event
                    emit:(void (^)(NSDictionary<NSString *, id> *event))emit;
@end

@interface WSRecorderActivityController : NSObject
- (instancetype)initWithBegin:(id (^)(void))begin
                           end:(void (^)(id activity))end;
- (void)begin;
- (void)end;
@end

@interface WSMicrophoneReconnectController : NSObject
- (instancetype)initWithMaximumAttempts:(NSInteger)maximumAttempts;
- (NSUInteger)begin;
- (BOOL)shouldAttemptGeneration:(NSUInteger)generation;
- (BOOL)recordFailureForGeneration:(NSUInteger)generation;
- (BOOL)recordSuccessForGeneration:(NSUInteger)generation;
- (void)cancel;
@end

void WSFinalizeSystemAudioOutput(dispatch_block_t removeOutput,
                                 dispatch_queue_t audioQueue,
                                 dispatch_block_t closeWriter);

int main() {
    @autoreleasepool {
        assert(whosaid_recorder_api_version() == 1);

        assert(!WSMicFailureIsFatal(WSMicStartResultDenied));
        assert(!WSMicFailureIsFatal(WSMicStartResultUnavailable));
        assert(!WSMicFailureIsFatal(WSMicStartResultInterrupted));
        assert(WSMicrophoneFailureAllowsReconnect(false, false));
        assert(!WSMicrophoneFailureAllowsReconnect(true, false));
        assert(!WSMicrophoneFailureAllowsReconnect(false, true));

        AVAudioFormat *stereo44100 = [[AVAudioFormat alloc]
            initStandardFormatWithSampleRate:44'100
                                     channels:2];
        AVAudioPCMBuffer *stereoBuffer = [[AVAudioPCMBuffer alloc]
            initWithPCMFormat:stereo44100
                frameCapacity:4'410];
        stereoBuffer.frameLength = 4'410;
        for (AVAudioChannelCount channel = 0; channel < stereo44100.channelCount; ++channel) {
            for (AVAudioFrameCount frame = 0; frame < stereoBuffer.frameLength; ++frame) {
                stereoBuffer.floatChannelData[channel][frame] =
                    channel == 0 ? 0.25f : 0.5f;
            }
        }
        NSError *conversionError = nil;
        AVAudioPCMBuffer *converted = WSConvertMicrophoneBuffer(stereoBuffer,
                                                                &conversionError);
        assert(conversionError == nil);
        assert(converted != nil);
        assert(std::fabs(converted.format.sampleRate - 48'000) < 0.5);
        assert(converted.format.channelCount == 1);
        assert(converted.frameLength == 4'800);
        BOOL hasSamples = NO;
        for (AVAudioFrameCount frame = 0; frame < converted.frameLength; ++frame) {
            if (std::fabs(converted.floatChannelData[0][frame]) > 0.0001f) {
                hasSamples = YES;
                break;
            }
        }
        assert(hasSamples);

        NSDictionary<NSString *, id> *stoppedManifest = WSSessionManifest(
            @"session-id", @1'786'500'000.0, YES, @"stopped", @"active");
        assert([stoppedManifest[@"complete"] isEqual:@NO]);
        assert([stoppedManifest[@"microphoneTrack"] isEqual:@"microphone.caf"]);

        NSError *persistenceError = [NSError
            errorWithDomain:@"com.yideng.whosaid.tests"
                       code:1
                   userInfo:@{NSLocalizedDescriptionKey : @"disk full"}];
        NSArray<NSDictionary<NSString *, id> *> *failedSourceEvents =
            WSSourceStatusEventsAfterPersistence(NO, @"microphone", @"active",
                                                  persistenceError);
        assert(failedSourceEvents.count == 1);
        assert([failedSourceEvents.firstObject[@"type"] isEqual:@"fatal_error"]);
        assert([failedSourceEvents.firstObject[@"message"] containsString:@"disk full"]);
        NSArray<NSDictionary<NSString *, id> *> *successfulSourceEvents =
            WSSourceStatusEventsAfterPersistence(YES, @"microphone", @"active", nil);
        assert(successfulSourceEvents.count == 1);
        assert([successfulSourceEvents.firstObject[@"type"] isEqual:@"source_status"]);

        NSArray<NSDictionary<NSString *, id> *> *failedStopEvents =
            WSStopTerminalEventsAfterPersistence(NO, @"/sessions/a", @"/sessions/a/system.caf",
                                                  nil, persistenceError);
        assert(failedStopEvents.count == 1);
        assert([failedStopEvents.firstObject[@"type"] isEqual:@"fatal_error"]);
        assert([failedStopEvents.firstObject[@"message"] containsString:@"停止会话清单"]);
        NSArray<NSDictionary<NSString *, id> *> *successfulStopEvents =
            WSStopTerminalEventsAfterPersistence(YES, @"/sessions/a", @"/sessions/a/system.caf",
                                                  nil, nil);
        assert(successfulStopEvents.count == 1);
        assert([successfulStopEvents.firstObject[@"type"] isEqual:@"stopped"]);
        assert(successfulStopEvents.firstObject[@"microphoneTrack"] == NSNull.null);

        WSRecorderSessionGate fatalCleanupGate;
        assert(fatalCleanupGate.claim());
        WSRecorderSessionGate *fatalCleanupGatePointer = &fatalCleanupGate;
        WSRecorderTerminalController *startupFailureController =
            [WSRecorderTerminalController new];
        NSMutableArray<NSString *> *cleanupOrder = [NSMutableArray array];
        [startupFailureController
            failWithMessage:@"manifest failed"
                    prepare:^{
                        [cleanupOrder addObject:@"prepare"];
                    }
                 stopSystem:^(void (^completion)(NSError *error)) {
                     [cleanupOrder addObject:@"stop_system"];
                     completion(nil);
                 }
                    cleanup:^{
                        [cleanupOrder addObject:@"cleanup_writers"];
                    }
                       emit:^(NSDictionary<NSString *, id> *event) {
                           [cleanupOrder addObject:event[@"type"]];
                       }
                    release:^{
                        fatalCleanupGatePointer->release();
                        [cleanupOrder addObject:@"release_gate"];
                    }];
        NSArray<NSString *> *expectedCleanupOrder = @[
            @"prepare", @"stop_system", @"cleanup_writers", @"fatal_error",
            @"release_gate"
        ];
        assert([cleanupOrder isEqual:expectedCleanupOrder]);
        assert(fatalCleanupGate.claim());

        WSRecorderTerminalController *microphoneFailureController =
            [WSRecorderTerminalController new];
        __block NSInteger microphoneFatalEvents = 0;
        __block NSInteger stoppedAfterFatalEvents = 0;
        [microphoneFailureController
            failWithMessage:@"microphone manifest failed"
                    prepare:^{}
                 stopSystem:^(void (^completion)(NSError *error)) {
                     completion(nil);
                 }
                    cleanup:^{}
                       emit:^(__unused NSDictionary<NSString *, id> *event) {
                           microphoneFatalEvents += 1;
                       }
                    release:^{}];
        BOOL emittedStoppedAfterFatal = [microphoneFailureController
            emitStoppedEvent:@{@"type" : @"stopped"}
                         emit:^(__unused NSDictionary<NSString *, id> *event) {
                             stoppedAfterFatalEvents += 1;
                         }];
        assert(microphoneFatalEvents == 1);
        assert(!emittedStoppedAfterFatal);
        assert(stoppedAfterFatalEvents == 0);
        assert(![microphoneFailureController allowsSuccess]);

        WSRecorderTerminalController *multipleFailureController =
            [WSRecorderTerminalController new];
        __block void (^pendingStopCompletion)(NSError *error) = nil;
        __block NSInteger stopAttempts = 0;
        __block NSInteger fatalEvents = 0;
        __block NSString *fatalMessage = nil;
        void (^deferredStop)(void (^)(NSError *error)) =
            ^(void (^completion)(NSError *error)) {
                stopAttempts += 1;
                pendingStopCompletion = [completion copy];
            };
        void (^countFatal)(NSDictionary<NSString *, id> *event) =
            ^(NSDictionary<NSString *, id> *event) {
                fatalEvents += 1;
                fatalMessage = event[@"message"];
            };
        [multipleFailureController failWithMessage:@"first"
                                           prepare:^{}
                                        stopSystem:deferredStop
                                           cleanup:^{}
                                              emit:countFatal
                                           release:^{}];
        [multipleFailureController failWithMessage:@"second"
                                           prepare:^{}
                                        stopSystem:deferredStop
                                           cleanup:^{}
                                              emit:countFatal
                                           release:^{}];
        assert(stopAttempts == 1);
        assert(fatalEvents == 0);
        assert(![multipleFailureController
            emitStoppedEvent:@{@"type" : @"stopped"}
                         emit:^(__unused NSDictionary<NSString *, id> *event) {
                             stoppedAfterFatalEvents += 1;
                         }]);
        assert(pendingStopCompletion != nil);
        NSError *stopFailure = [NSError
            errorWithDomain:@"com.yideng.whosaid.tests"
                       code:2
                   userInfo:@{NSLocalizedDescriptionKey : @"stop failed"}];
        pendingStopCompletion(stopFailure);
        assert(fatalEvents == 1);
        assert([fatalMessage containsString:@"stop failed"]);

        dispatch_queue_t audioQueue = dispatch_queue_create(
            "com.yideng.whosaid.tests.system-audio", DISPATCH_QUEUE_SERIAL);
        dispatch_queue_t finalizerQueue = dispatch_queue_create(
            "com.yideng.whosaid.tests.finalizer", DISPATCH_QUEUE_SERIAL);
        dispatch_semaphore_t callbackStarted = dispatch_semaphore_create(0);
        dispatch_semaphore_t releaseCallback = dispatch_semaphore_create(0);
        dispatch_semaphore_t finalizerReachedDrain = dispatch_semaphore_create(0);
        dispatch_semaphore_t finalizerFinished = dispatch_semaphore_create(0);
        auto callbackFinished = std::make_shared<std::atomic_bool>(false);
        auto outputRemoved = std::make_shared<std::atomic_bool>(false);
        auto writerClosed = std::make_shared<std::atomic_bool>(false);
        auto writerClosedAfterCallback = std::make_shared<std::atomic_bool>(false);
        auto writerClosedAfterRemoval = std::make_shared<std::atomic_bool>(false);
        dispatch_async(audioQueue, ^{
            dispatch_semaphore_signal(callbackStarted);
            dispatch_semaphore_wait(releaseCallback, DISPATCH_TIME_FOREVER);
            callbackFinished->store(true);
        });
        assert(dispatch_semaphore_wait(
                   callbackStarted,
                   dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
        dispatch_async(finalizerQueue, ^{
            WSFinalizeSystemAudioOutput(
                ^{
                    outputRemoved->store(true);
                    dispatch_semaphore_signal(finalizerReachedDrain);
                },
                audioQueue, ^{
                    writerClosedAfterCallback->store(callbackFinished->load());
                    writerClosedAfterRemoval->store(outputRemoved->load());
                    writerClosed->store(true);
                });
            dispatch_semaphore_signal(finalizerFinished);
        });
        assert(dispatch_semaphore_wait(
                   finalizerReachedDrain,
                   dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
        assert(dispatch_semaphore_wait(finalizerFinished, DISPATCH_TIME_NOW) != 0);
        assert(outputRemoved->load());
        assert(!writerClosed->load());
        dispatch_semaphore_signal(releaseCallback);
        assert(dispatch_semaphore_wait(
                   finalizerFinished,
                   dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
        assert(writerClosed->load());
        assert(writerClosedAfterCallback->load());
        assert(writerClosedAfterRemoval->load());

        __block NSInteger normalActivityBegins = 0;
        __block NSInteger normalActivityEnds = 0;
        NSObject *normalActivityToken = [NSObject new];
        WSRecorderActivityController *normalActivity =
            [[WSRecorderActivityController alloc]
                initWithBegin:^id {
                    normalActivityBegins += 1;
                    return normalActivityToken;
                }
                         end:^(__unused id activity) {
                             normalActivityEnds += 1;
                         }];
        [normalActivity begin];
        [normalActivity begin];
        [normalActivity end];
        [normalActivity end];
        assert(normalActivityBegins == 1);
        assert(normalActivityEnds == 1);

        __block NSInteger fatalActivityBegins = 0;
        __block NSInteger fatalActivityEnds = 0;
        NSObject *fatalActivityToken = [NSObject new];
        WSRecorderActivityController *fatalActivity =
            [[WSRecorderActivityController alloc]
                initWithBegin:^id {
                    fatalActivityBegins += 1;
                    return fatalActivityToken;
                }
                         end:^(__unused id activity) {
                             fatalActivityEnds += 1;
                         }];
        WSRecorderTerminalController *activityFatalController =
            [WSRecorderTerminalController new];
        __block void (^activityStopCompletion)(NSError *error) = nil;
        [fatalActivity begin];
        [activityFatalController
            failWithMessage:@"fatal"
                    prepare:^{}
                 stopSystem:^(void (^completion)(NSError *error)) {
                     activityStopCompletion = [completion copy];
                 }
                    cleanup:^{
                        [fatalActivity end];
                    }
                       emit:^(__unused NSDictionary<NSString *, id> *event) {}
                    release:^{}];
        assert(fatalActivityBegins == 1);
        assert(fatalActivityEnds == 0);
        assert(activityStopCompletion != nil);
        activityStopCompletion(nil);
        activityStopCompletion(nil);
        [fatalActivity end];
        assert(fatalActivityEnds == 1);

        __block NSInteger startupFailureActivityBegins = 0;
        __block NSInteger startupFailureActivityEnds = 0;
        NSObject *startupFailureActivityToken = [NSObject new];
        WSRecorderActivityController *startupFailureActivity =
            [[WSRecorderActivityController alloc]
                initWithBegin:^id {
                    startupFailureActivityBegins += 1;
                    return startupFailureActivityToken;
                }
                         end:^(__unused id activity) {
                             startupFailureActivityEnds += 1;
                         }];
        WSRecorderTerminalController *activityStartupFailureController =
            [WSRecorderTerminalController new];
        [startupFailureActivity begin];
        [activityStartupFailureController
            failWithMessage:@"startup persistence failed"
                    prepare:^{}
                 stopSystem:^(void (^completion)(NSError *error)) {
                     completion(nil);
                 }
                    cleanup:^{
                        [startupFailureActivity end];
                    }
                       emit:^(__unused NSDictionary<NSString *, id> *event) {}
                    release:^{}];
        [startupFailureActivity end];
        assert(startupFailureActivityBegins == 1);
        assert(startupFailureActivityEnds == 1);

        __block NSInteger destructionActivityBegins = 0;
        __block NSInteger destructionActivityEnds = 0;
        @autoreleasepool {
            NSObject *destructionActivityToken = [NSObject new];
            WSRecorderActivityController *destructionActivity =
                [[WSRecorderActivityController alloc]
                    initWithBegin:^id {
                        destructionActivityBegins += 1;
                        return destructionActivityToken;
                    }
                             end:^(__unused id activity) {
                                 destructionActivityEnds += 1;
                             }];
            [destructionActivity begin];
            assert(destructionActivityBegins == 1);
        }
        assert(destructionActivityEnds == 1);

        WSMicrophoneReconnectController *reconnect =
            [[WSMicrophoneReconnectController alloc] initWithMaximumAttempts:3];
        NSUInteger firstGeneration = [reconnect begin];
        assert([reconnect shouldAttemptGeneration:firstGeneration]);
        assert([reconnect recordFailureForGeneration:firstGeneration]);
        assert([reconnect shouldAttemptGeneration:firstGeneration]);
        assert([reconnect recordSuccessForGeneration:firstGeneration]);
        assert(![reconnect shouldAttemptGeneration:firstGeneration]);

        NSUInteger currentGeneration = [reconnect begin];
        NSUInteger repeatedNotificationGeneration = [reconnect begin];
        assert(repeatedNotificationGeneration == currentGeneration);
        assert([reconnect shouldAttemptGeneration:currentGeneration]);
        assert([reconnect recordFailureForGeneration:currentGeneration]);
        assert([reconnect recordFailureForGeneration:currentGeneration]);
        assert(![reconnect recordFailureForGeneration:currentGeneration]);
        assert(![reconnect shouldAttemptGeneration:currentGeneration]);

        NSUInteger replacementGeneration = [reconnect begin];
        assert(replacementGeneration != currentGeneration);
        assert(![reconnect recordFailureForGeneration:currentGeneration]);
        assert([reconnect shouldAttemptGeneration:replacementGeneration]);

        NSUInteger cancelledGeneration = [reconnect begin];
        [reconnect cancel];
        assert(![reconnect shouldAttemptGeneration:cancelledGeneration]);
        assert(![reconnect recordSuccessForGeneration:cancelledGeneration]);

        assert(std::strcmp(whosaid_system_audio_permission_state(true, false), "granted") == 0);
        assert(std::strcmp(whosaid_system_audio_permission_state(true, true), "granted") == 0);
        assert(std::strcmp(whosaid_system_audio_permission_state(false, false),
                           "notDetermined") == 0);
        assert(std::strcmp(whosaid_system_audio_permission_state(false, true), "denied") == 0);

        assert(WSSilenceFrames(1'000'000'000, 1'250'000'000, 48'000) == 12'000);
        assert(WSSilenceFrames(1'000'000'000, 999'000'000, 48'000) == 0);

        WSRecorderSessionGate sessionGate;
        assert(sessionGate.claim());
        assert(!sessionGate.claim());
        sessionGate.release();
        assert(sessionGate.claim());

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
