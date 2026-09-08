#include "../WindowsTimeline.h"
#include <cassert>
#include <iostream>
#include <iterator>
#include <limits>

int main(int argc, char** argv) {
    assert(argc == 2);
    auto path = std::filesystem::u8path(argv[1]);
    {
        whosaid::WindowsTimeline track(path);
        float data[] = {0.5f, -0.5f, std::numeric_limits<float>::quiet_NaN(), 2.0f};
        assert(track.append(480, data, 4, false) == 1.0f);
        assert(track.size() == 484);
        track.append(480, data, 4, false); // duplicate packet is trimmed
        assert(track.size() == 484);
        track.append(484, nullptr, 480, true);
        track.silence_to(48000);
        track.flush();
    }
    assert(std::filesystem::file_size(path) == 68 + 48000 * 4);
    std::ifstream file(path, std::ios::binary);
    char header[4]; file.read(header, 4); assert(std::string(header, 4) == "caff");
    bool rejected = false;
    try { whosaid::WindowsTimeline track(path); } catch (...) { rejected = true; }
    assert(rejected);
    std::cout << "Windows timeline tests passed\n";
}
