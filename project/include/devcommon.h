#ifndef DEVCOMMON
#define DEVCOMMON

#include <iostream>
#include <map>
#include <memory>
#include <string.h>
#include <unordered_map>
#include <vector>
#include <fstream>
#include <algorithm>
#include <unordered_set>
#include <random>
#include <set>

namespace ECProject {
inline bool is_azure_lrc_family(const std::string &code_type) {
    return code_type == "AzureLRC" || code_type == "SRS" || code_type == "ERS";
}
}

#endif