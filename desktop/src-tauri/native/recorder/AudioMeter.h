#pragma once

#import <AVFoundation/AVFoundation.h>
#include <algorithm>
#include <cmath>
#include <optional>

// 每路采集队列独占一个实例；只发送峰值，不传输或修改原始音频。
class WSAudioMeter {
    double seconds = 0;
    float peak = 0;
public:
    std::optional<float> consume(AVAudioPCMBuffer *buffer) {
        if (!buffer || !buffer.frameLength || buffer.format.sampleRate <= 0) return {};
        const auto floats = buffer.floatChannelData;
        const auto ints16 = buffer.int16ChannelData;
        const auto ints32 = buffer.int32ChannelData;
        const auto stride = buffer.stride;
        const auto frames = buffer.frameLength;
        const bool interleaved = buffer.format.interleaved;
        for (AVAudioChannelCount channel = 0; channel < buffer.format.channelCount; ++channel) {
            const auto plane = interleaved ? 0 : channel;
            const auto offset = interleaved ? channel : 0;
            for (AVAudioFrameCount frame = 0; frame < frames; ++frame) {
                const auto index = frame * stride + offset;
                float value = 0;
                if (floats) value = floats[plane][index];
                else if (ints16) value = ints16[plane][index] / 32768.0f;
                else if (ints32) value = ints32[plane][index] / 2147483648.0f;
                if (std::isfinite(value)) peak = std::max(peak, std::min(1.0f, std::abs(value)));
            }
        }
        seconds += buffer.frameLength / buffer.format.sampleRate;
        if (seconds < 0.1) return {};
        const float result = peak;
        seconds = 0;
        peak = 0;
        return result;
    }
};
