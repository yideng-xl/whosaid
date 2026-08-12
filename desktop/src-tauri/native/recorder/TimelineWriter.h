#pragma once

#import <AVFoundation/AVFoundation.h>

#include <stdint.h>

uint64_t WSSilenceFrames(uint64_t expectedHostNs,
                         uint64_t actualHostNs,
                         uint32_t sampleRate);

@interface WSTimelineWriter : NSObject
- (instancetype)initWithURL:(NSURL *)url
                  sampleRate:(double)sampleRate
                sessionStart:(uint64_t)sessionStartNs
                       error:(NSError **)error;
- (BOOL)appendBuffer:(AVAudioPCMBuffer *)buffer
          receivedAt:(uint64_t)hostNs
               error:(NSError **)error;
- (BOOL)close:(NSError **)error;
@end
