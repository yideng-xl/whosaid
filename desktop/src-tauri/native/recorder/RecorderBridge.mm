#import "RecorderBridge.h"
#import "RecorderPermissionState.h"
#import "RecorderSessionGate.h"
#import "TimelineWriter.h"

#import <AppKit/AppKit.h>
#import <AVFoundation/AVFoundation.h>
#import <CoreGraphics/CGWindow.h>
#import <CoreMedia/CoreMedia.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>

#import <mach/mach_time.h>

#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <cstring>
#include <limits>

enum WSMicStartResult {
    WSMicStartResultDenied,
    WSMicStartResultUnavailable,
    WSMicStartResultInterrupted,
};

bool WSMicFailureIsFatal(WSMicStartResult result) {
    switch (result) {
    case WSMicStartResultDenied:
    case WSMicStartResultUnavailable:
    case WSMicStartResultInterrupted:
        return false;
    }
    return true;
}

namespace {

NSError *recorder_error(NSInteger code, NSString *message) {
    return [NSError errorWithDomain:@"com.yideng.whosaid.recorder"
                               code:code
                           userInfo:@{NSLocalizedDescriptionKey : message}];
}

AVAudioPCMBuffer *convert_microphone_buffer(AVAudioPCMBuffer *buffer,
                                            AVAudioConverter *converter,
                                            NSError **error) {
    if (buffer == nil || buffer.frameLength == 0 || converter == nil ||
        buffer.format.sampleRate <= 0 || buffer.format.channelCount == 0) {
        if (error != nullptr) {
            *error = recorder_error(12, @"麦克风音频格式无效");
        }
        return nil;
    }

    AVAudioFormat *targetFormat = converter.outputFormat;
    const double ratio = targetFormat.sampleRate / buffer.format.sampleRate;
    const double capacityValue = std::ceil(buffer.frameLength * ratio);
    if (!std::isfinite(capacityValue) || capacityValue <= 0 ||
        capacityValue > std::numeric_limits<AVAudioFrameCount>::max()) {
        if (error != nullptr) {
            *error = recorder_error(14, @"麦克风缓冲区帧数超限");
        }
        return nil;
    }

    AVAudioPCMBuffer *converted = [[AVAudioPCMBuffer alloc]
        initWithPCMFormat:targetFormat
            frameCapacity:static_cast<AVAudioFrameCount>(capacityValue)];
    if (converted == nil) {
        if (error != nullptr) {
            *error = recorder_error(15, @"无法创建麦克风转换缓冲区");
        }
        return nil;
    }

    __block BOOL suppliedInput = NO;
    AVAudioConverterOutputStatus status = [converter
        convertToBuffer:converted
                   error:error
      withInputFromBlock:^AVAudioBuffer *(AVAudioPacketCount requestedPackets,
                                          AVAudioConverterInputStatus *inputStatus) {
          (void)requestedPackets;
          if (suppliedInput) {
              *inputStatus = AVAudioConverterInputStatus_NoDataNow;
              return nil;
          }
          suppliedInput = YES;
          *inputStatus = AVAudioConverterInputStatus_HaveData;
          return buffer;
      }];
    if (status == AVAudioConverterOutputStatus_Error || converted.frameLength == 0) {
        if (error != nullptr && *error == nil) {
            *error = recorder_error(15, @"无法转换麦克风缓冲区");
        }
        return nil;
    }
    return converted;
}

NSDictionary<NSString *, id> *persistence_failure_event(NSString *context,
                                                         NSError *error) {
    NSString *detail = error.localizedDescription.length > 0
                           ? error.localizedDescription
                           : @"未知错误";
    return @{
        @"type" : @"fatal_error",
        @"message" : [NSString stringWithFormat:@"无法持久化“%@”：%@", context, detail],
    };
}

} // namespace

AVAudioPCMBuffer *WSConvertMicrophoneBuffer(AVAudioPCMBuffer *buffer, NSError **error) {
    if (buffer == nil || buffer.format.sampleRate <= 0 ||
        buffer.format.channelCount == 0) {
        if (error != nullptr) {
            *error = recorder_error(12, @"麦克风音频格式无效");
        }
        return nil;
    }
    AVAudioFormat *targetFormat = [[AVAudioFormat alloc]
        initStandardFormatWithSampleRate:48'000 channels:1];
    AVAudioConverter *converter = [[AVAudioConverter alloc]
        initFromFormat:buffer.format
              toFormat:targetFormat];
    converter.primeMethod = AVAudioConverterPrimeMethod_None;
    return convert_microphone_buffer(buffer, converter, error);
}

NSDictionary<NSString *, id> *WSSessionManifest(NSString *sessionID,
                                                 NSNumber *startedAt,
                                                 BOOL microphoneHasFrames,
                                                 NSString *systemStatus,
                                                 NSString *microphoneStatus) {
    return @{
        @"schemaVersion" : @1,
        @"sessionId" : sessionID,
        @"startedAt" : startedAt,
        @"systemTrack" : @"system.caf",
        @"microphoneTrack" : microphoneHasFrames ? @"microphone.caf" : NSNull.null,
        @"systemStatus" : systemStatus,
        @"microphoneStatus" : microphoneStatus,
        @"complete" : @NO,
    };
}

NSArray<NSDictionary<NSString *, id> *> *WSSourceStatusEventsAfterPersistence(
    BOOL persisted,
    NSString *source,
    NSString *status,
    NSError *error) {
    if (!persisted) {
        NSString *context = [NSString stringWithFormat:@"%@ %@ 状态", source, status];
        return @[ persistence_failure_event(context, error) ];
    }
    return @[ @{
        @"type" : @"source_status",
        @"source" : source,
        @"status" : status,
    } ];
}

NSArray<NSDictionary<NSString *, id> *> *WSStopTerminalEventsAfterPersistence(
    BOOL persisted,
    NSString *sessionDirectory,
    NSString *systemTrack,
    NSString *microphoneTrack,
    NSError *error) {
    if (!persisted) {
        return @[ persistence_failure_event(@"停止会话清单", error) ];
    }
    return @[ @{
        @"type" : @"stopped",
        @"sessionDir" : sessionDirectory,
        @"systemTrack" : systemTrack,
        @"microphoneTrack" : microphoneTrack != nil ? microphoneTrack : NSNull.null,
    } ];
}

typedef NS_ENUM(NSInteger, WSRecorderTerminalState) {
    WSRecorderTerminalStateOpen,
    WSRecorderTerminalStateFatal,
    WSRecorderTerminalStateStopped,
};

@interface WSRecorderTerminalController : NSObject {
    WSRecorderTerminalState _state;
    BOOL _fatalDelivered;
}
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

@implementation WSRecorderTerminalController

- (instancetype)init {
    self = [super init];
    if (self != nil) {
        _state = WSRecorderTerminalStateOpen;
    }
    return self;
}

- (BOOL)allowsSuccess {
    @synchronized(self) {
        return _state == WSRecorderTerminalStateOpen;
    }
}

- (void)failWithMessage:(NSString *)message
                prepare:(dispatch_block_t)prepare
             stopSystem:(void (^)(void (^completion)(NSError *error)))stopSystem
                cleanup:(dispatch_block_t)cleanup
                   emit:(void (^)(NSDictionary<NSString *, id> *event))emit
                release:(dispatch_block_t)release {
    @synchronized(self) {
        if (_state != WSRecorderTerminalStateOpen) {
            return;
        }
        _state = WSRecorderTerminalStateFatal;
    }

    if (prepare != nil) {
        prepare();
    }
    void (^completion)(NSError *) = ^(NSError *stopError) {
        @synchronized(self) {
            if (_fatalDelivered) {
                return;
            }
            _fatalDelivered = YES;
        }
        if (cleanup != nil) {
            cleanup();
        }
        NSString *finalMessage = message.length > 0 ? message : @"录音会话失败";
        if (stopError != nil) {
            finalMessage = [NSString
                stringWithFormat:@"%@；系统声音停止失败：%@", finalMessage,
                                 stopError.localizedDescription ?: @"未知错误"];
        }
        if (emit != nil) {
            emit(@{ @"type" : @"fatal_error", @"message" : finalMessage });
        }
        if (release != nil) {
            release();
        }
    };
    if (stopSystem != nil) {
        stopSystem(completion);
    } else {
        completion(nil);
    }
}

- (BOOL)emitStoppedEvent:(NSDictionary<NSString *, id> *)event
                    emit:(void (^)(NSDictionary<NSString *, id> *event))emit {
    @synchronized(self) {
        if (_state != WSRecorderTerminalStateOpen) {
            return NO;
        }
        _state = WSRecorderTerminalStateStopped;
    }
    if (emit != nil) {
        emit(event);
    }
    return YES;
}

@end

@interface WSRecorderActivityController : NSObject {
    id (^_beginActivity)(void);
    void (^_endActivity)(id activity);
    id _activity;
    BOOL _beginAttempted;
    BOOL _ended;
}
- (instancetype)initWithBegin:(id (^)(void))begin
                           end:(void (^)(id activity))end;
- (void)begin;
- (void)end;
@end

@implementation WSRecorderActivityController

- (instancetype)initWithBegin:(id (^)(void))begin
                           end:(void (^)(id activity))end {
    self = [super init];
    if (self != nil) {
        _beginActivity = [begin copy];
        _endActivity = [end copy];
    }
    return self;
}

- (void)begin {
    @synchronized(self) {
        if (_beginAttempted || _ended) {
            return;
        }
        _beginAttempted = YES;
        if (_beginActivity != nil) {
            _activity = _beginActivity();
        }
    }
}

- (void)end {
    id activity = nil;
    void (^endActivity)(id activity) = nil;
    @synchronized(self) {
        if (_ended) {
            return;
        }
        _ended = YES;
        activity = _activity;
        _activity = nil;
        endActivity = _endActivity;
    }
    if (activity != nil && endActivity != nil) {
        endActivity(activity);
    }
}

- (void)dealloc {
    [self end];
}

@end

void WSFinalizeSystemAudioOutput(dispatch_block_t removeOutput,
                                 dispatch_queue_t audioQueue,
                                 dispatch_block_t closeWriter) {
    if (removeOutput != nil) {
        removeOutput();
    }
    if (audioQueue != nil) {
        dispatch_sync(audioQueue, ^{});
    }
    if (closeWriter != nil) {
        closeWriter();
    }
}

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

uint64_t monotonic_nanoseconds() {
    static mach_timebase_info_data_t timebase = [] {
        mach_timebase_info_data_t value = {};
        mach_timebase_info(&value);
        return value;
    }();
    const __uint128_t ticks = mach_continuous_time();
    return static_cast<uint64_t>(ticks * timebase.numer / timebase.denom);
}

NSString *microphone_status(WSMicStartResult result) {
    switch (result) {
    case WSMicStartResultDenied:
        return @"denied";
    case WSMicStartResultUnavailable:
        return @"unavailable";
    case WSMicStartResultInterrupted:
        return @"interrupted";
    }
    return @"unavailable";
}

} // namespace

@class WSSystemAudioRecorder;

namespace {

WSSystemAudioRecorder *active_recorder = nil;
WSRecorderSessionGate session_gate;

NSObject *active_recorder_lock() {
    static NSObject *lock = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        lock = [NSObject new];
    });
    return lock;
}

void clear_active_recorder(WSSystemAudioRecorder *recorder) {
    @synchronized(active_recorder_lock()) {
        if (active_recorder == recorder) {
            active_recorder = nil;
            session_gate.release();
        }
    }
}

} // namespace

@interface WSSystemAudioRecorder : NSObject <SCStreamOutput, SCStreamDelegate>
@property(nonatomic, strong) NSURL *sessionURL;
@property(nonatomic, copy) NSString *sessionID;
@property(nonatomic, strong) NSNumber *startedAt;
@property(nonatomic, strong) SCStream *stream;
@property(nonatomic, strong) WSTimelineWriter *writer;
@property(nonatomic, strong) AVAudioEngine *microphoneEngine;
@property(nonatomic, strong) AVAudioConverter *microphoneConverter;
@property(nonatomic, strong) WSTimelineWriter *microphoneWriter;
@property(nonatomic, strong) NSObject *microphoneLock;
@property(nonatomic, strong) id microphoneConfigurationObserver;
@property(nonatomic, strong) WSRecorderTerminalController *terminalController;
@property(nonatomic, strong) WSRecorderActivityController *activityController;
@property(nonatomic, strong) dispatch_queue_t stateQueue;
@property(nonatomic, strong) dispatch_queue_t audioQueue;
@property(nonatomic, strong) dispatch_source_t elapsedTimer;
@property(nonatomic, copy) NSString *systemStatus;
@property(nonatomic, copy) NSString *microphoneStatus;
@property(nonatomic, assign) WhosaidRecorderCallback callback;
@property(nonatomic, assign) void *callbackContext;
@property(nonatomic, assign) uint64_t sessionStartNs;
@property(nonatomic, assign) BOOL starting;
@property(nonatomic, assign) BOOL recording;
@property(nonatomic, assign) BOOL finished;
@property(nonatomic, assign) BOOL microphoneTapInstalled;
@property(atomic, assign) BOOL microphoneHasFrames;
@property(atomic, assign) BOOL stopping;
@property(atomic, assign) BOOL microphoneStopping;
- (instancetype)initWithSessionDirectory:(const char *)sessionDirectory
                                callback:(WhosaidRecorderCallback)callback
                                 context:(void *)context;
- (BOOL)begin;
- (void)stop;
- (void)finalizeCaptureResources;
@end

@implementation WSSystemAudioRecorder

- (instancetype)initWithSessionDirectory:(const char *)sessionDirectory
                                callback:(WhosaidRecorderCallback)callback
                                 context:(void *)context {
    self = [super init];
    if (self == nil) {
        return nil;
    }
    NSString *path = sessionDirectory == nullptr
                         ? nil
                         : [NSString stringWithUTF8String:sessionDirectory];
    if (path.length == 0 || callback == nullptr) {
        return nil;
    }
    _sessionURL = [NSURL fileURLWithPath:path isDirectory:YES];
    _sessionID = NSUUID.UUID.UUIDString;
    _startedAt = @([[NSDate date] timeIntervalSince1970]);
    _callback = callback;
    _callbackContext = context;
    _microphoneLock = [NSObject new];
    _terminalController = [WSRecorderTerminalController new];
    _activityController = [[WSRecorderActivityController alloc]
        initWithBegin:^id {
            return [[NSProcessInfo processInfo]
                beginActivityWithOptions:NSActivityIdleSystemSleepDisabled
                                  reason:@"WhoSaid 正在录制系统声音"];
        }
                 end:^(id activity) {
                     [[NSProcessInfo processInfo] endActivity:activity];
                 }];
    _stateQueue = dispatch_queue_create("com.yideng.whosaid.recorder.state",
                                        DISPATCH_QUEUE_SERIAL);
    _audioQueue = dispatch_queue_create("com.yideng.whosaid.recorder.system-audio",
                                        DISPATCH_QUEUE_SERIAL);
    _sessionStartNs = monotonic_nanoseconds();
    _starting = YES;
    _systemStatus = @"starting";
    _microphoneStatus = @"unavailable";
    return self;
}

- (void)emit:(NSDictionary<NSString *, id> *)event {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:event options:0 error:&error];
    if (data == nil || error != nil) {
        return;
    }
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (json != nil && self.callback != nullptr) {
        self.callback(json.UTF8String, self.callbackContext);
    }
}

- (void)dealloc {
    [self.activityController end];
}

- (NSURL *)sessionFileURL:(NSString *)filename {
    if (filename.length == 0 || [filename containsString:@"/"]) {
        return nil;
    }
    NSURL *url = [self.sessionURL URLByAppendingPathComponent:filename isDirectory:NO];
    if (![url.URLByDeletingLastPathComponent.path isEqualToString:self.sessionURL.path]) {
        return nil;
    }
    return url;
}

- (BOOL)writeSessionManifest:(NSError **)error {
    NSURL *manifestURL = [self sessionFileURL:@"session.json"];
    if (manifestURL == nil) {
        if (error != nullptr) {
            *error = [NSError errorWithDomain:@"com.yideng.whosaid.recorder"
                                          code:11
                                      userInfo:@{NSLocalizedDescriptionKey : @"会话清单路径无效"}];
        }
        return NO;
    }

    NSDictionary<NSString *, id> *manifest = WSSessionManifest(
        self.sessionID, self.startedAt, self.microphoneHasFrames, self.systemStatus,
        self.microphoneStatus);
    NSData *data = [NSJSONSerialization dataWithJSONObject:manifest options:0 error:error];
    return data != nil && [data writeToURL:manifestURL options:NSDataWritingAtomic error:error];
}

- (void)emitEvents:(NSArray<NSDictionary<NSString *, id> *> *)events {
    for (NSDictionary<NSString *, id> *event in events) {
        [self emit:event];
    }
}

- (void)beginFatalTerminationWithMessage:(NSString *)message
                   removeSessionDirectory:(BOOL)removeSessionDirectory
                                stopSystem:(BOOL)stopSystem {
    __weak WSSystemAudioRecorder *weakSelf = self;
    [self.terminalController
        failWithMessage:message
                prepare:^{
                    WSSystemAudioRecorder *strongSelf = weakSelf;
                    strongSelf.stopping = YES;
                    strongSelf.starting = NO;
                    strongSelf.recording = NO;
                    strongSelf.finished = YES;
                    [strongSelf cancelElapsedTimer];
                }
             stopSystem:^(void (^completion)(NSError *error)) {
                 WSSystemAudioRecorder *strongSelf = weakSelf;
                 SCStream *stream = strongSelf.stream;
                 if (!stopSystem || stream == nil) {
                     completion(nil);
                     return;
                 }
                 [stream stopCaptureWithCompletionHandler:^(NSError *error) {
                     dispatch_async(strongSelf.stateQueue, ^{
                         completion(error);
                     });
                 }];
             }
                cleanup:^{
                    WSSystemAudioRecorder *strongSelf = weakSelf;
                    [strongSelf finalizeCaptureResources];
                    if (removeSessionDirectory) {
                        [strongSelf removeSessionDirectory];
                    }
                }
                   emit:^(NSDictionary<NSString *, id> *event) {
                       WSSystemAudioRecorder *strongSelf = weakSelf;
                       [strongSelf emit:event];
                   }
                release:^{
                    WSSystemAudioRecorder *strongSelf = weakSelf;
                    if (strongSelf != nil) {
                        clear_active_recorder(strongSelf);
                    }
                }];
}

- (void)terminateForPersistenceContext:(NSString *)context error:(NSError *)error {
    NSString *message = persistence_failure_event(context, error)[@"message"];
    [self beginFatalTerminationWithMessage:message
                    removeSessionDirectory:NO
                                 stopSystem:YES];
}

- (void)emitMicrophoneResult:(WSMicStartResult)result {
    if (WSMicFailureIsFatal(result)) {
        return;
    }
    self.microphoneStatus = microphone_status(result);
    NSError *manifestError = nil;
    const BOOL persisted = [self writeSessionManifest:&manifestError];
    if (!persisted) {
        [self terminateForPersistenceContext:
                  [NSString stringWithFormat:@"microphone %@ 状态", self.microphoneStatus]
                                      error:manifestError];
        return;
    }
    if ([self.terminalController allowsSuccess]) {
        [self emitEvents:WSSourceStatusEventsAfterPersistence(
                             YES, @"microphone", self.microphoneStatus, nil)];
    }
}

- (void)closeMicrophoneWriter {
    @synchronized(self.microphoneLock) {
        NSError *error = nil;
        [self.microphoneWriter close:&error];
        self.microphoneWriter = nil;
        self.microphoneConverter = nil;
    }
}

- (void)stopMicrophoneCapture {
    self.microphoneStopping = YES;
    if (self.microphoneConfigurationObserver != nil) {
        [[NSNotificationCenter defaultCenter]
            removeObserver:self.microphoneConfigurationObserver];
        self.microphoneConfigurationObserver = nil;
    }

    AVAudioEngine *engine = self.microphoneEngine;
    if (engine != nil && self.microphoneTapInstalled) {
        @try {
            [engine.inputNode removeTapOnBus:0];
        } @catch (__unused NSException *exception) {
        }
        self.microphoneTapInstalled = NO;
    }
    [engine stop];
    self.microphoneEngine = nil;
    [self closeMicrophoneWriter];
}

- (void)interruptMicrophone {
    if (self.stopping || self.finished ||
        [self.microphoneStatus isEqualToString:@"interrupted"] ||
        [self.microphoneStatus isEqualToString:@"denied"] ||
        [self.microphoneStatus isEqualToString:@"unavailable"]) {
        return;
    }
    [self stopMicrophoneCapture];
    [self emitMicrophoneResult:WSMicStartResultInterrupted];
}

- (void)appendMicrophoneBuffer:(AVAudioPCMBuffer *)buffer receivedAt:(uint64_t)hostNs {
    if (buffer == nil || buffer.frameLength == 0 || self.microphoneStopping ||
        self.stopping) {
        return;
    }

    __block NSError *failure = nil;
    __block BOOL firstValidFrames = NO;
    @synchronized(self.microphoneLock) {
        if (self.microphoneStopping || self.stopping) {
            return;
        }

        AVAudioFormat *sourceFormat = buffer.format;
        AVAudioFormat *targetFormat = [[AVAudioFormat alloc]
            initStandardFormatWithSampleRate:48'000 channels:1];
        if (sourceFormat.sampleRate <= 0 || sourceFormat.channelCount == 0 ||
            targetFormat == nil) {
            failure = [NSError errorWithDomain:@"com.yideng.whosaid.recorder"
                                           code:12
                                       userInfo:@{NSLocalizedDescriptionKey : @"麦克风音频格式无效"}];
        } else {
            if (self.microphoneConverter == nil ||
                ![self.microphoneConverter.inputFormat isEqual:sourceFormat]) {
                self.microphoneConverter = [[AVAudioConverter alloc]
                    initFromFormat:sourceFormat
                          toFormat:targetFormat];
                self.microphoneConverter.primeMethod = AVAudioConverterPrimeMethod_None;
            }
            if (self.microphoneConverter == nil) {
                failure = [NSError errorWithDomain:@"com.yideng.whosaid.recorder"
                                               code:13
                                           userInfo:@{NSLocalizedDescriptionKey : @"无法转换麦克风音频格式"}];
            }
        }

        AVAudioPCMBuffer *converted = nil;
        if (failure == nil) {
            converted = convert_microphone_buffer(buffer, self.microphoneConverter,
                                                  &failure);
        }

        if (failure == nil && converted.frameLength > 0) {
            if (self.microphoneWriter == nil) {
                NSURL *microphoneURL = [self sessionFileURL:@"microphone.caf"];
                self.microphoneWriter = [[WSTimelineWriter alloc]
                    initWithURL:microphoneURL
                     sampleRate:48'000
                   sessionStart:self.sessionStartNs
                          error:&failure];
            }
            if (self.microphoneWriter != nil &&
                ![self.microphoneWriter appendBuffer:converted
                                          receivedAt:hostNs
                                               error:&failure]) {
                if (failure == nil) {
                    failure = [NSError errorWithDomain:@"com.yideng.whosaid.recorder"
                                                   code:17
                                               userInfo:@{NSLocalizedDescriptionKey : @"无法写入麦克风音轨"}];
                }
                NSError *closeError = nil;
                [self.microphoneWriter close:&closeError];
                self.microphoneWriter = nil;
                if (!self.microphoneHasFrames) {
                    [[NSFileManager defaultManager]
                        removeItemAtURL:[self sessionFileURL:@"microphone.caf"]
                                 error:nil];
                }
            } else if (self.microphoneWriter == nil && failure == nil) {
                failure = [NSError errorWithDomain:@"com.yideng.whosaid.recorder"
                                               code:18
                                           userInfo:@{NSLocalizedDescriptionKey : @"无法创建麦克风音轨"}];
            } else if (failure == nil && self.microphoneWriter != nil &&
                       !self.microphoneHasFrames) {
                self.microphoneHasFrames = YES;
                firstValidFrames = YES;
            }
        }
    }

    if (failure != nil) {
        self.microphoneStopping = YES;
        dispatch_async(self.stateQueue, ^{
            [self interruptMicrophone];
        });
        return;
    }
    if (firstValidFrames) {
        dispatch_async(self.stateQueue, ^{
            if (!self.finished && !self.stopping &&
                [self.terminalController allowsSuccess]) {
                NSError *manifestError = nil;
                if (![self writeSessionManifest:&manifestError]) {
                    [self terminateForPersistenceContext:@"麦克风首帧清单"
                                                  error:manifestError];
                }
            }
        });
    }
}

- (void)startMicrophoneEngine {
    if (self.stopping || self.finished) {
        return;
    }

    AVAudioEngine *engine = [AVAudioEngine new];
    AVAudioInputNode *inputNode = engine.inputNode;
    AVAudioFormat *format = [inputNode inputFormatForBus:0];
    if (format.sampleRate <= 0 || format.channelCount == 0) {
        [self emitMicrophoneResult:WSMicStartResultUnavailable];
        return;
    }

    self.microphoneEngine = engine;
    self.microphoneStopping = NO;
    __weak WSSystemAudioRecorder *weakSelf = self;
    @try {
        [inputNode installTapOnBus:0
                       bufferSize:4096
                           format:nil
                            block:^(AVAudioPCMBuffer *buffer, __unused AVAudioTime *when) {
                                WSSystemAudioRecorder *strongSelf = weakSelf;
                                [strongSelf appendMicrophoneBuffer:buffer
                                                       receivedAt:monotonic_nanoseconds()];
                            }];
        self.microphoneTapInstalled = YES;
    } @catch (__unused NSException *exception) {
        [self stopMicrophoneCapture];
        [self emitMicrophoneResult:WSMicStartResultUnavailable];
        return;
    }

    self.microphoneConfigurationObserver = [[NSNotificationCenter defaultCenter]
        addObserverForName:AVAudioEngineConfigurationChangeNotification
                    object:engine
                     queue:nil
                usingBlock:^(__unused NSNotification *notification) {
                    WSSystemAudioRecorder *strongSelf = weakSelf;
                    if (strongSelf != nil) {
                        dispatch_async(strongSelf.stateQueue, ^{
                            [strongSelf interruptMicrophone];
                        });
                    }
                }];

    NSError *startError = nil;
    if (![engine startAndReturnError:&startError]) {
        [self stopMicrophoneCapture];
        [self emitMicrophoneResult:WSMicStartResultUnavailable];
        return;
    }

    self.microphoneStatus = @"active";
    NSError *manifestError = nil;
    const BOOL persisted = [self writeSessionManifest:&manifestError];
    if (!persisted) {
        [self terminateForPersistenceContext:@"microphone active 状态"
                                      error:manifestError];
        return;
    }
    if ([self.terminalController allowsSuccess]) {
        [self emitEvents:WSSourceStatusEventsAfterPersistence(
                             YES, @"microphone", @"active", nil)];
    }
}

- (void)startMicrophone {
    AVAuthorizationStatus status =
        [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
    if (status == AVAuthorizationStatusDenied ||
        status == AVAuthorizationStatusRestricted) {
        [self emitMicrophoneResult:WSMicStartResultDenied];
        return;
    }
    if (status == AVAuthorizationStatusNotDetermined) {
        __weak WSSystemAudioRecorder *weakSelf = self;
        [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio
                                 completionHandler:^(BOOL granted) {
                                     WSSystemAudioRecorder *strongSelf = weakSelf;
                                     if (strongSelf == nil) {
                                         return;
                                     }
                                     dispatch_async(strongSelf.stateQueue, ^{
                                         if (strongSelf.stopping || strongSelf.finished) {
                                             return;
                                         }
                                         if (granted) {
                                             [strongSelf startMicrophoneEngine];
                                         } else {
                                             [strongSelf emitMicrophoneResult:
                                                                 WSMicStartResultDenied];
                                         }
                                     });
                                 }];
        return;
    }
    [self startMicrophoneEngine];
}

- (void)closeWriter {
    NSError *error = nil;
    [self.writer close:&error];
    self.writer = nil;
}

- (void)finalizeCaptureResources {
    SCStream *stream = self.stream;
    __weak WSSystemAudioRecorder *weakSelf = self;
    WSFinalizeSystemAudioOutput(
        ^{
            if (stream != nil) {
                NSError *removeOutputError = nil;
                [stream removeStreamOutput:weakSelf
                                      type:SCStreamOutputTypeAudio
                                     error:&removeOutputError];
            }
        },
        self.audioQueue, ^{
            WSSystemAudioRecorder *strongSelf = weakSelf;
            [strongSelf stopMicrophoneCapture];
            [strongSelf closeWriter];
        });
    self.stream = nil;
    [self.activityController end];
}

- (void)cancelElapsedTimer {
    if (self.elapsedTimer != nil) {
        dispatch_source_cancel(self.elapsedTimer);
        self.elapsedTimer = nil;
    }
}

- (void)removeSessionDirectory {
    [[NSFileManager defaultManager] removeItemAtURL:self.sessionURL error:nil];
}

- (void)failStartup:(NSError *)error {
    if (self.stopping || ![self.terminalController allowsSuccess]) {
        return;
    }
    [self beginFatalTerminationWithMessage:error.localizedDescription
                                                ?: @"系统声音启动失败"
                    removeSessionDirectory:YES
                                 stopSystem:self.stream != nil];
}

- (void)failDuringRecording:(NSError *)error {
    if (self.stopping || ![self.terminalController allowsSuccess]) {
        return;
    }
    self.systemStatus = @"interrupted";
    NSError *manifestError = nil;
    NSString *message = error.localizedDescription ?: @"系统声音录制中断";
    if (![self writeSessionManifest:&manifestError]) {
        message = persistence_failure_event(@"系统声音中断清单", manifestError)[@"message"];
    }
    [self beginFatalTerminationWithMessage:message
                    removeSessionDirectory:NO
                                 stopSystem:YES];
}

- (void)failAudioWithCode:(NSInteger)code message:(NSString *)message {
    NSError *error = [NSError errorWithDomain:@"com.yideng.whosaid.recorder"
                                         code:code
                                     userInfo:@{NSLocalizedDescriptionKey : message}];
    dispatch_async(self.stateQueue, ^{
        [self failDuringRecording:error];
    });
}

- (BOOL)prepareSession:(NSError **)error {
    NSFileManager *manager = [NSFileManager defaultManager];
    BOOL isDirectory = NO;
    if ([manager fileExistsAtPath:self.sessionURL.path isDirectory:&isDirectory]) {
        if (!isDirectory) {
            if (error != nullptr) {
                *error = [NSError errorWithDomain:@"com.yideng.whosaid.recorder"
                                              code:1
                                          userInfo:@{NSLocalizedDescriptionKey : @"会话路径不是目录"}];
            }
            return NO;
        }
    } else if (![manager createDirectoryAtURL:self.sessionURL
                  withIntermediateDirectories:YES
                                   attributes:nil
                                        error:error]) {
        return NO;
    }

    NSURL *systemAudioURL = [self sessionFileURL:@"system.caf"];
    if (systemAudioURL == nil) {
        if (error != nullptr) {
            *error = [NSError errorWithDomain:@"com.yideng.whosaid.recorder"
                                          code:16
                                      userInfo:@{NSLocalizedDescriptionKey : @"系统音轨路径无效"}];
        }
        return NO;
    }
    self.writer = [[WSTimelineWriter alloc] initWithURL:systemAudioURL
                                             sampleRate:48'000
                                           sessionStart:self.sessionStartNs
                                                  error:error];
    return self.writer != nil && [self writeSessionManifest:error];
}

- (BOOL)begin {
    [self emit:@{@"type" : @"starting"}];

    NSError *error = nil;
    if (![self prepareSession:&error]) {
        [self failStartup:error];
        return NO;
    }
    if (!CGPreflightScreenCaptureAccess() &&
        whosaid_recorder_request_system_audio_permission() == 0) {
        error = [NSError errorWithDomain:@"com.yideng.whosaid.recorder"
                                    code:2
                                userInfo:@{NSLocalizedDescriptionKey : @"未获得系统录音权限"}];
        [self failStartup:error];
        return NO;
    }

    [SCShareableContent getShareableContentWithCompletionHandler:^(
                            SCShareableContent *content, NSError *contentError) {
        dispatch_async(self.stateQueue, ^{
            if (self.stopping) {
                return;
            }
            if (contentError != nil || content.displays.count == 0) {
                NSError *failure = contentError ?: [NSError
                    errorWithDomain:@"com.yideng.whosaid.recorder"
                               code:3
                           userInfo:@{NSLocalizedDescriptionKey : @"找不到可采集的显示器"}];
                [self failStartup:failure];
                return;
            }

            SCDisplay *display = nil;
            const CGDirectDisplayID mainDisplayID = CGMainDisplayID();
            for (SCDisplay *candidate in content.displays) {
                if (candidate.displayID == mainDisplayID) {
                    display = candidate;
                    break;
                }
            }
            if (display == nil) {
                display = content.displays.firstObject;
            }

            SCContentFilter *filter = [[SCContentFilter alloc] initWithDisplay:display
                                                              excludingWindows:@[]];
            SCStreamConfiguration *config = [SCStreamConfiguration new];
            config.capturesAudio = YES;
            config.excludesCurrentProcessAudio = YES;
            config.sampleRate = 48000;
            config.channelCount = 1;
            config.width = 2;
            config.height = 2;
            config.minimumFrameInterval = CMTimeMake(1, 1);

            self.stream = [[SCStream alloc] initWithFilter:filter
                                             configuration:config
                                                  delegate:self];
            NSError *outputError = nil;
            if (![self.stream addStreamOutput:self
                                         type:SCStreamOutputTypeAudio
                           sampleHandlerQueue:self.audioQueue
                                        error:&outputError]) {
                [self failStartup:outputError];
                return;
            }

            [self.stream startCaptureWithCompletionHandler:^(NSError *startError) {
                dispatch_async(self.stateQueue, ^{
                    if (self.stopping) {
                        return;
                    }
                    if (startError != nil) {
                        [self failStartup:startError];
                        return;
                    }

                    [self.activityController begin];
                    self.starting = NO;
                    self.recording = YES;
                    self.systemStatus = @"active";
                    NSError *manifestError = nil;
                    if (![self writeSessionManifest:&manifestError]) {
                        [self terminateForPersistenceContext:@"system active 状态"
                                                      error:manifestError];
                        return;
                    }
                    if (![self.terminalController allowsSuccess]) {
                        return;
                    }
                    [self emit:@{
                        @"type" : @"recording",
                        @"startedAt" : self.startedAt
                    }];
                    [self emit:@{
                        @"type" : @"source_status",
                        @"source" : @"system",
                        @"status" : @"active"
                    }];
                    [self startElapsedTimer];
                    [self startMicrophone];
                });
            }];
        });
    }];
    return YES;
}

- (void)startElapsedTimer {
    self.elapsedTimer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0,
                                               self.stateQueue);
    dispatch_source_set_timer(self.elapsedTimer,
                              dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC),
                              NSEC_PER_SEC, NSEC_PER_MSEC * 100);
    dispatch_source_set_event_handler(self.elapsedTimer, ^{
        if (![self.terminalController allowsSuccess]) {
            return;
        }
        const uint64_t now = monotonic_nanoseconds();
        const uint64_t elapsed = now > self.sessionStartNs
                                     ? (now - self.sessionStartNs) / NSEC_PER_SEC
                                     : 0;
        [self emit:@{@"type" : @"elapsed", @"elapsedSeconds" : @(elapsed)}];
    });
    dispatch_resume(self.elapsedTimer);
}

- (void)finishStoppedSession {
    if (self.finished) {
        return;
    }
    self.finished = YES;
    self.recording = NO;
    self.starting = NO;
    self.systemStatus = @"stopped";
    [self finalizeCaptureResources];

    NSURL *systemTrackURL = [self sessionFileURL:@"system.caf"];
    NSURL *microphoneTrackURL = self.microphoneHasFrames
                                    ? [self sessionFileURL:@"microphone.caf"]
                                    : nil;
    NSError *manifestError = nil;
    const BOOL persisted = [self writeSessionManifest:&manifestError];
    if (!persisted) {
        NSString *message = persistence_failure_event(@"停止会话清单",
                                                       manifestError)[@"message"];
        [self beginFatalTerminationWithMessage:message
                        removeSessionDirectory:NO
                                     stopSystem:NO];
        return;
    }

    NSDictionary<NSString *, id> *stoppedEvent =
        WSStopTerminalEventsAfterPersistence(YES, self.sessionURL.path,
                                             systemTrackURL.path,
                                             microphoneTrackURL.path, nil).firstObject;
    [self.terminalController emitStoppedEvent:stoppedEvent
                                         emit:^(NSDictionary<NSString *, id> *event) {
                                             [self emit:event];
                                         }];
    clear_active_recorder(self);
}

- (void)stop {
    dispatch_async(self.stateQueue, ^{
        if (self.stopping) {
            return;
        }
        self.stopping = YES;
        self.starting = NO;
        self.recording = NO;
        [self cancelElapsedTimer];
        SCStream *stream = self.stream;
        if (stream == nil) {
            [self finishStoppedSession];
            return;
        }
        [stream stopCaptureWithCompletionHandler:^(NSError *error) {
            dispatch_async(self.stateQueue, ^{
                if (error != nil) {
                    NSString *message = [NSString
                        stringWithFormat:@"无法停止系统声音：%@",
                                         error.localizedDescription ?: @"未知错误"];
                    [self beginFatalTerminationWithMessage:message
                                    removeSessionDirectory:NO
                                                 stopSystem:NO];
                    return;
                }
                [self finishStoppedSession];
            });
        }];
    });
}

- (void)stream:(SCStream *)stream
    didOutputSampleBuffer:(CMSampleBufferRef)sampleBuffer
                   ofType:(SCStreamOutputType)type {
    (void)stream;
    if (type != SCStreamOutputTypeAudio || self.stopping ||
        !CMSampleBufferDataIsReady(sampleBuffer)) {
        return;
    }

    CMAudioFormatDescriptionRef description =
        (CMAudioFormatDescriptionRef)CMSampleBufferGetFormatDescription(sampleBuffer);
    if (description == nullptr) {
        [self failAudioWithCode:4 message:@"无法读取系统声音格式"];
        return;
    }
    const AudioStreamBasicDescription *streamDescription =
        CMAudioFormatDescriptionGetStreamBasicDescription(description);
    if (streamDescription == nullptr) {
        [self failAudioWithCode:4 message:@"无法读取系统声音格式"];
        return;
    }
    AVAudioFormat *format = [[AVAudioFormat alloc] initWithStreamDescription:streamDescription];
    if (format == nil) {
        [self failAudioWithCode:5 message:@"系统声音格式无效"];
        return;
    }

    size_t bufferListSize = 0;
    OSStatus status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sampleBuffer, &bufferListSize, nullptr, 0, nullptr, nullptr, 0, nullptr);
    if (status != noErr || bufferListSize == 0) {
        [self failAudioWithCode:6 message:@"无法读取系统声音缓冲区"];
        return;
    }
    AudioBufferList *allocatedBufferList =
        static_cast<AudioBufferList *>(std::malloc(bufferListSize));
    if (allocatedBufferList == nullptr) {
        [self failAudioWithCode:7 message:@"无法分配系统声音缓冲区"];
        return;
    }
    CMBlockBufferRef blockBuffer = nullptr;
    status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sampleBuffer, nullptr, allocatedBufferList, bufferListSize, kCFAllocatorDefault,
        kCFAllocatorDefault, kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
        &blockBuffer);
    if (status != noErr) {
        std::free(allocatedBufferList);
        if (blockBuffer != nullptr) {
            CFRelease(blockBuffer);
        }
        [self failAudioWithCode:8 message:@"无法复制系统声音缓冲区"];
        return;
    }

    const CMItemCount sampleCount = CMSampleBufferGetNumSamples(sampleBuffer);
    if (sampleCount <= 0) {
        std::free(allocatedBufferList);
        if (blockBuffer != nullptr) {
            CFRelease(blockBuffer);
        }
        return;
    }
    if (static_cast<uint64_t>(sampleCount) >
        std::numeric_limits<AVAudioFrameCount>::max()) {
        std::free(allocatedBufferList);
        if (blockBuffer != nullptr) {
            CFRelease(blockBuffer);
        }
        [self failAudioWithCode:9 message:@"系统声音缓冲区帧数超限"];
        return;
    }
    const AVAudioFrameCount frameCount = static_cast<AVAudioFrameCount>(sampleCount);
    AVAudioPCMBuffer *audioBuffer = [[AVAudioPCMBuffer alloc] initWithPCMFormat:format
                                                                 frameCapacity:frameCount];
    if (audioBuffer == nil) {
        std::free(allocatedBufferList);
        if (blockBuffer != nullptr) {
            CFRelease(blockBuffer);
        }
        [self failAudioWithCode:9 message:@"无法创建系统声音 PCM 缓冲区"];
        return;
    }
    audioBuffer.frameLength = frameCount;

    AudioBufferList *ownedBufferList = audioBuffer.mutableAudioBufferList;
    BOOL copied = ownedBufferList->mNumberBuffers == allocatedBufferList->mNumberBuffers;
    for (UInt32 index = 0; copied && index < ownedBufferList->mNumberBuffers; ++index) {
        const AudioBuffer source = allocatedBufferList->mBuffers[index];
        AudioBuffer destination = ownedBufferList->mBuffers[index];
        copied = source.mData != nullptr && destination.mData != nullptr &&
                 source.mDataByteSize == destination.mDataByteSize;
        if (copied) {
            std::memcpy(destination.mData, source.mData, source.mDataByteSize);
        }
    }
    std::free(allocatedBufferList);
    if (blockBuffer != nullptr) {
        CFRelease(blockBuffer);
    }
    if (!copied) {
        [self failAudioWithCode:10 message:@"系统声音缓冲区布局不匹配"];
        return;
    }

    NSError *writeError = nil;
    if (![self.writer appendBuffer:audioBuffer
                        receivedAt:monotonic_nanoseconds()
                             error:&writeError]) {
        dispatch_async(self.stateQueue, ^{
            [self failDuringRecording:writeError];
        });
    }
}

- (void)stream:(SCStream *)stream didStopWithError:(NSError *)error {
    (void)stream;
    dispatch_async(self.stateQueue, ^{
        if (!self.stopping) {
            if (self.starting) {
                [self failStartup:error];
            } else {
                [self failDuringRecording:error];
            }
        }
    });
}

@end

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
    @synchronized(active_recorder_lock()) {
        if (!session_gate.claim()) {
            return -1;
        }
        WSSystemAudioRecorder *recorder =
            [[WSSystemAudioRecorder alloc] initWithSessionDirectory:session_dir
                                                          callback:callback
                                                           context:context];
        if (recorder == nil) {
            session_gate.release();
            return -1;
        }
        active_recorder = recorder;
        if (![recorder begin]) {
            return -1;
        }
        return 0;
    }
}

int32_t whosaid_recorder_stop(void) {
    @synchronized(active_recorder_lock()) {
        if (active_recorder == nil) {
            return -1;
        }
        [active_recorder stop];
        return 0;
    }
}

void whosaid_recorder_free_string(char *value) {
    std::free(value);
}
