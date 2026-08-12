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
#include <cstring>
#include <limits>

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
@property(nonatomic, strong) SCStream *stream;
@property(nonatomic, strong) WSTimelineWriter *writer;
@property(nonatomic, strong) dispatch_queue_t stateQueue;
@property(nonatomic, strong) dispatch_queue_t audioQueue;
@property(nonatomic, strong) dispatch_source_t elapsedTimer;
@property(nonatomic, assign) WhosaidRecorderCallback callback;
@property(nonatomic, assign) void *callbackContext;
@property(nonatomic, assign) uint64_t sessionStartNs;
@property(nonatomic, assign) BOOL starting;
@property(nonatomic, assign) BOOL recording;
@property(atomic, assign) BOOL stopping;
- (instancetype)initWithSessionDirectory:(const char *)sessionDirectory
                                callback:(WhosaidRecorderCallback)callback
                                 context:(void *)context;
- (BOOL)begin;
- (void)stop;
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
    _callback = callback;
    _callbackContext = context;
    _stateQueue = dispatch_queue_create("com.yideng.whosaid.recorder.state",
                                        DISPATCH_QUEUE_SERIAL);
    _audioQueue = dispatch_queue_create("com.yideng.whosaid.recorder.system-audio",
                                        DISPATCH_QUEUE_SERIAL);
    _sessionStartNs = monotonic_nanoseconds();
    _starting = YES;
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

- (void)closeWriter {
    NSError *error = nil;
    [self.writer close:&error];
    self.writer = nil;
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
    if (self.stopping) {
        return;
    }
    self.stopping = YES;
    self.starting = NO;
    [self cancelElapsedTimer];
    [self closeWriter];
    self.stream = nil;
    [self removeSessionDirectory];
    [self emit:@{
        @"type" : @"fatal_error",
        @"message" : error.localizedDescription ?: @"系统声音启动失败"
    }];
    clear_active_recorder(self);
}

- (void)failDuringRecording:(NSError *)error {
    if (self.stopping) {
        return;
    }
    self.stopping = YES;
    self.recording = NO;
    [self cancelElapsedTimer];
    [self closeWriter];
    [self emit:@{
        @"type" : @"fatal_error",
        @"message" : error.localizedDescription ?: @"系统声音录制中断"
    }];
    SCStream *stream = self.stream;
    if (stream != nil) {
        [stream stopCaptureWithCompletionHandler:^(__unused NSError *stopError) {
            dispatch_async(self.stateQueue, ^{
                self.stream = nil;
                clear_active_recorder(self);
            });
        }];
    } else {
        clear_active_recorder(self);
    }
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

    NSURL *systemAudioURL = [self.sessionURL URLByAppendingPathComponent:@"system.caf"];
    self.writer = [[WSTimelineWriter alloc] initWithURL:systemAudioURL
                                             sampleRate:48'000
                                           sessionStart:self.sessionStartNs
                                                  error:error];
    return self.writer != nil;
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
                [self closeWriter];
                [self removeSessionDirectory];
                clear_active_recorder(self);
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
                        [self closeWriter];
                        [self removeSessionDirectory];
                        clear_active_recorder(self);
                        return;
                    }
                    if (startError != nil) {
                        [self failStartup:startError];
                        return;
                    }

                    self.starting = NO;
                    self.recording = YES;
                    [self emit:@{
                        @"type" : @"recording",
                        @"startedAt" : @([[NSDate date] timeIntervalSince1970])
                    }];
                    [self emit:@{
                        @"type" : @"source_status",
                        @"source" : @"system",
                        @"status" : @"active"
                    }];
                    [self startElapsedTimer];
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
        const uint64_t now = monotonic_nanoseconds();
        const uint64_t elapsed = now > self.sessionStartNs
                                     ? (now - self.sessionStartNs) / NSEC_PER_SEC
                                     : 0;
        [self emit:@{@"type" : @"elapsed", @"elapsedSeconds" : @(elapsed)}];
    });
    dispatch_resume(self.elapsedTimer);
}

- (void)stop {
    dispatch_async(self.stateQueue, ^{
        if (self.stopping) {
            return;
        }
        const BOOL wasStarting = self.starting;
        const BOOL wasRecording = self.recording;
        self.stopping = YES;
        self.starting = NO;
        self.recording = NO;
        [self cancelElapsedTimer];
        SCStream *stream = self.stream;
        if (stream == nil) {
            [self closeWriter];
            [self removeSessionDirectory];
            clear_active_recorder(self);
            return;
        }
        [stream stopCaptureWithCompletionHandler:^(NSError *error) {
            dispatch_async(self.stateQueue, ^{
                if (error != nil) {
                    self.starting = wasStarting;
                    self.recording = wasRecording;
                    self.stopping = NO;
                    if (wasRecording) {
                        [self startElapsedTimer];
                    }
                    [self emit:@{
                        @"type" : @"fatal_error",
                        @"message" : [NSString stringWithFormat:@"无法停止系统声音：%@",
                                                                  error.localizedDescription ?: @"未知错误"]
                    }];
                    return;
                }
                [self closeWriter];
                self.stream = nil;
                clear_active_recorder(self);
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
