#import "TimelineWriter.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace {

NSString *const WSTimelineWriterErrorDomain = @"com.yideng.whosaid.timeline-writer";

NSError *writer_error(NSInteger code, NSString *message) {
    return [NSError errorWithDomain:WSTimelineWriterErrorDomain
                               code:code
                           userInfo:@{NSLocalizedDescriptionKey : message}];
}

} // namespace

uint64_t WSSilenceFrames(uint64_t expectedHostNs,
                         uint64_t actualHostNs,
                         uint32_t sampleRate) {
    if (actualHostNs <= expectedHostNs || sampleRate == 0) {
        return 0;
    }

    const __uint128_t elapsed = actualHostNs - expectedHostNs;
    const __uint128_t frames = elapsed * sampleRate / 1'000'000'000ULL;
    return frames > std::numeric_limits<uint64_t>::max()
               ? std::numeric_limits<uint64_t>::max()
               : static_cast<uint64_t>(frames);
}

@interface WSTimelineWriter ()
@property(nonatomic, strong) AVAudioFile *file;
@property(nonatomic, strong) AVAudioFormat *format;
@property(nonatomic, assign) uint64_t sessionStartNs;
@property(nonatomic, assign) uint64_t framesWritten;
@property(nonatomic, assign) BOOL closed;
@end

@implementation WSTimelineWriter

- (instancetype)initWithURL:(NSURL *)url
                  sampleRate:(double)sampleRate
                sessionStart:(uint64_t)sessionStartNs
                       error:(NSError **)error {
    self = [super init];
    if (self == nil) {
        return nil;
    }

    if (url == nil || !std::isfinite(sampleRate) || sampleRate <= 0 ||
        sampleRate > std::numeric_limits<uint32_t>::max()) {
        if (error != nullptr) {
            *error = writer_error(1, @"无效的时间轴写入参数");
        }
        return nil;
    }

    _format = [[AVAudioFormat alloc] initStandardFormatWithSampleRate:sampleRate channels:1];
    _file = [[AVAudioFile alloc] initForWriting:url
                                      settings:_format.settings
                                  commonFormat:AVAudioPCMFormatFloat32
                                   interleaved:NO
                                         error:error];
    if (_file == nil) {
        return nil;
    }

    _sessionStartNs = sessionStartNs;
    _framesWritten = 0;
    _closed = NO;
    return self;
}

- (BOOL)writeSilenceFrames:(uint64_t)frameCount error:(NSError **)error {
    while (frameCount > 0) {
        const AVAudioFrameCount chunk = static_cast<AVAudioFrameCount>(
            std::min<uint64_t>(frameCount, 48'000));
        AVAudioPCMBuffer *silence = [[AVAudioPCMBuffer alloc] initWithPCMFormat:self.format
                                                                 frameCapacity:chunk];
        if (silence == nil) {
            if (error != nullptr) {
                *error = writer_error(2, @"无法分配静音缓冲区");
            }
            return NO;
        }
        silence.frameLength = chunk;
        for (AVAudioChannelCount channel = 0; channel < self.format.channelCount; ++channel) {
            std::fill_n(silence.floatChannelData[channel], chunk, 0.0f);
        }
        if (![self.file writeFromBuffer:silence error:error]) {
            return NO;
        }
        self.framesWritten += chunk;
        frameCount -= chunk;
    }
    return YES;
}

- (BOOL)appendBuffer:(AVAudioPCMBuffer *)buffer
          receivedAt:(uint64_t)hostNs
               error:(NSError **)error {
    if (self.closed || self.file == nil) {
        if (error != nullptr) {
            *error = writer_error(3, @"时间轴写入器已关闭");
        }
        return NO;
    }
    if (buffer == nil || buffer.frameLength == 0) {
        return YES;
    }
    if (buffer.format.channelCount != 1 ||
        std::fabs(buffer.format.sampleRate - self.format.sampleRate) > 0.5) {
        if (error != nullptr) {
            *error = writer_error(4, @"音频缓冲区必须是 48 kHz 单声道 PCM");
        }
        return NO;
    }

    const uint64_t sampleRate = static_cast<uint64_t>(std::llround(self.format.sampleRate));
    const __uint128_t writtenNs =
        static_cast<__uint128_t>(self.framesWritten) * 1'000'000'000ULL / sampleRate;
    const uint64_t expectedHostNs =
        writtenNs > std::numeric_limits<uint64_t>::max() - self.sessionStartNs
            ? std::numeric_limits<uint64_t>::max()
            : self.sessionStartNs + static_cast<uint64_t>(writtenNs);
    uint64_t missingFrames = WSSilenceFrames(expectedHostNs, hostNs,
                                              static_cast<uint32_t>(sampleRate));
    if (missingFrames < buffer.frameLength) {
        missingFrames = 0;
    }
    if (![self writeSilenceFrames:missingFrames error:error]) {
        return NO;
    }

    AVAudioPCMBuffer *output = buffer;
    if (buffer.format.commonFormat != AVAudioPCMFormatFloat32 || buffer.format.isInterleaved) {
        AVAudioConverter *converter = [[AVAudioConverter alloc] initFromFormat:buffer.format
                                                                      toFormat:self.format];
        if (converter == nil) {
            if (error != nullptr) {
                *error = writer_error(5, @"无法创建音频格式转换器");
            }
            return NO;
        }
        output = [[AVAudioPCMBuffer alloc] initWithPCMFormat:self.format
                                              frameCapacity:buffer.frameLength];
        if (output == nil || ![converter convertToBuffer:output fromBuffer:buffer error:error]) {
            return NO;
        }
    }

    if (![self.file writeFromBuffer:output error:error]) {
        return NO;
    }
    self.framesWritten += output.frameLength;
    return YES;
}

- (BOOL)close:(NSError **)error {
    if (self.closed) {
        return YES;
    }
    self.closed = YES;
    self.file = nil;
    (void)error;
    return YES;
}

@end
