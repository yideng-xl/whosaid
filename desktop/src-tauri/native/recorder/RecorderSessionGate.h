#pragma once

class WSRecorderSessionGate {
public:
    bool claim() {
        if (claimed_) {
            return false;
        }
        claimed_ = true;
        return true;
    }

    void release() {
        claimed_ = false;
    }

private:
    bool claimed_ = false;
};
