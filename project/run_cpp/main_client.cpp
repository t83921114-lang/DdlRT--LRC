#include "client.h"
#include "toolbox.h"
#include <fstream>
#include <filesystem>
#include <sys/time.h>
#include <unistd.h>
#include <sys/stat.h>
#include <cstdlib>
#include "config.h"
#include <iomanip>
#include <iostream>
#include <chrono>
#include <algorithm>
#include <random>
#include <stdexcept>
#include "encoder.h"


int main(int argc, char **argv)
{
    namespace fs = std::filesystem;
    std::error_code path_error;
    fs::path executable_path = fs::canonical("/proc/self/exe", path_error);
    if (path_error) {
        executable_path = fs::absolute(argv[0], path_error);
    }
    if (path_error) {
        std::cerr << "Failed to resolve executable path: " << path_error.message() << std::endl;
        return 2;
    }
    const fs::path config_path =
        (executable_path.parent_path() / "../../config/parameterConfiguration.xml").lexically_normal();
    if (!fs::is_regular_file(config_path)) {
        std::cerr << "Config file does not exist: " << config_path << std::endl;
        return 2;
    }
    const std::string sys_config_path = config_path.string();
    std::cout << "Config file: " << sys_config_path << std::endl;

    const ECProject::Config *config = ECProject::Config::getInstance(sys_config_path);

    std::string coordinator_addr = config->CoordinatorIP + ":" + std::to_string(config->CoordinatorPort);
    int stripe_num = 1000;
    std::string test_mode = "merge";
    int failed_block_id = 0;
    bool coordinator_from_cli = false;
    for (int i = 1; i < argc; ++i) {
        const std::string argument = argv[i];
        if (argument == "--coordinator") {
            if (++i >= argc) {
                std::cerr << "Missing value for --coordinator" << std::endl;
                return 2;
            }
            coordinator_addr = argv[i];
            coordinator_from_cli = true;
        } else if (argument == "--stripes") {
            if (++i >= argc) {
                std::cerr << "Missing value for --stripes" << std::endl;
                return 2;
            }
            try {
                size_t consumed = 0;
                stripe_num = std::stoi(argv[i], &consumed);
                if (consumed != std::string(argv[i]).size() || stripe_num <= 0) {
                    throw std::invalid_argument("not a positive integer");
                }
            } catch (const std::exception &) {
                std::cerr << "Invalid --stripes value: " << argv[i] << std::endl;
                return 2;
            }
        } else if (argument == "--test-mode") {
            if (++i >= argc) {
                std::cerr << "Missing value for --test-mode" << std::endl;
                return 2;
            }
            test_mode = argv[i];
            if (test_mode != "merge" && test_mode != "normal-rw" &&
                test_mode != "recovery") {
                std::cerr << "Invalid --test-mode value: " << test_mode << std::endl;
                return 2;
            }
        } else if (argument == "--failed-block") {
            if (++i >= argc) {
                std::cerr << "Missing value for --failed-block" << std::endl;
                return 2;
            }
            try {
                size_t consumed = 0;
                failed_block_id = std::stoi(argv[i], &consumed);
                if (consumed != std::string(argv[i]).size() || failed_block_id < 0) {
                    throw std::invalid_argument("not a non-negative integer");
                }
            } catch (const std::exception &) {
                std::cerr << "Invalid --failed-block value: " << argv[i] << std::endl;
                return 2;
            }
        } else if (!argument.empty() && argument[0] != '-' && !coordinator_from_cli) {
            coordinator_addr = argument;
            coordinator_from_cli = true;
        } else if (argument == "--help" || argument == "-h") {
            std::cout << "Usage: " << argv[0]
                      << " [--coordinator HOST:PORT] [--stripes COUNT]"
                      << " [--test-mode merge|normal-rw|recovery]"
                      << " [--failed-block ID]" << std::endl;
            return 0;
        } else {
            std::cerr << "Unknown argument: " << argument << std::endl;
            return 2;
        }
    }
    if (coordinator_from_cli) {
        std::cout << "Using coordinator address (from argv): " << coordinator_addr << std::endl;
    } else {
        const char *env_addr = std::getenv("COORDINATOR_ADDR");
        if (env_addr && env_addr[0] != '\0') {
            coordinator_addr = env_addr;
            std::cout << "Using coordinator address (from COORDINATOR_ADDR): " << coordinator_addr << std::endl;
        }
    }
    std::cout << "Stripe count: " << stripe_num << std::endl;
    std::cout << "Test mode: " << test_mode << std::endl;
    if (test_mode != "merge" && stripe_num != 1) {
        std::cerr << test_mode << " requires --stripes 1" << std::endl;
        return 2;
    }

    std::string client_ip = "10.10.1.1";
    int client_port = 55555;
    ECProject::Client client(client_ip, client_port, coordinator_addr, sys_config_path);
    std::cout << client.sayHelloToCoordinatorByGrpc("Client ID: " + client_ip + ":" + std::to_string(client_port)) << std::endl;

    std::vector<int> parameters = client.get_parameters();
    int k = parameters[0];
    int r = parameters[1];
    int z = parameters[2];
    std::string code_type;
    if(parameters[4] == 0){
        code_type = "AzureLRC";
    }
    else if(parameters[4] == 1){
        code_type = "OptimalLRC";
    }
    else if(parameters[4] == 2){
        code_type = "UniformLRC";
    }
    else if(parameters[4] == 3){
        code_type = "UniLRC";
    }
    else if(parameters[4] == 4){
        code_type = "RS";
    }
    else if(parameters[4] == 5){
        code_type = "DdlRT_LRC";
    }
    else if(parameters[4] == 6){ code_type = "SRS"; }
    else if(parameters[4] == 7){ code_type = "ERS"; }
    else{
        std::cout << "Code type error" << std::endl;
        return -1;
    }
    const int block_size_bytes = parameters[3];
    const double block_size_mib = static_cast<double>(block_size_bytes) /
                                  (1024.0 * 1024.0);
    int n = k + r + z;

    const double logical_write_mib = static_cast<double>(stripe_num) * k * block_size_mib;
    std::cout << "Starting set stripe operation" << std::endl;
    const auto set_start = std::chrono::high_resolution_clock::now();
    for (int i = 0; i < stripe_num; i++) {
        if (!client.set()) {
            std::cerr << "set failed at stripe " << i << std::endl;
            return 1;
        }
    }
    const auto set_end = std::chrono::high_resolution_clock::now();
    const std::chrono::duration<double> set_time = set_end - set_start;
    std::cout << "Set stripe operation finished" << std::endl;
    std::cout << "write time: " << set_time.count() << " seconds" << std::endl;
    std::cout << "write throughput: " << logical_write_mib / set_time.count()
              << " MiB/s" << std::endl;

    if (test_mode == "normal-rw") {
        const auto read_start = std::chrono::high_resolution_clock::now();
        std::shared_ptr<char[]> data = client.get_blocks(0, k - 1);
        const auto read_end = std::chrono::high_resolution_clock::now();
        if (!data) {
            std::cerr << "normal read failed" << std::endl;
            return 1;
        }
        const std::chrono::duration<double> read_time = read_end - read_start;
        const double logical_read_mib = static_cast<double>(k) * block_size_mib;
        std::cout << "read time: " << read_time.count() << " seconds" << std::endl;
        std::cout << "read throughput: " << logical_read_mib / read_time.count()
                  << " MiB/s" << std::endl;
        return 0;
    }

    if (test_mode == "recovery") {
        if (failed_block_id >= n) {
            std::cerr << "failed block id is outside the initial stripe: "
                      << failed_block_id << std::endl;
            return 2;
        }
        const auto recovery_start = std::chrono::high_resolution_clock::now();
        const bool recovered = client.recovery(0, failed_block_id);
        const auto recovery_end = std::chrono::high_resolution_clock::now();
        if (!recovered) {
            std::cerr << "single-block recovery failed" << std::endl;
            return 1;
        }
        const std::chrono::duration<double> recovery_time = recovery_end - recovery_start;
        std::cout << "recovery block: " << failed_block_id << std::endl;
        std::cout << "recovery time: " << recovery_time.count() << " seconds" << std::endl;
        std::cout << "recovery throughput: " << block_size_mib / recovery_time.count()
                  << " MiB/s" << std::endl;
        return 0;
    }

    std::cout << "\n[Merge bandwidth] if you want to limit the bandwidth during merge, please execute the following commands:\n"
              << "  before merge please execute: sh limit_bandwidth.sh\n"
              << "  after merge please execute: sh unlimit_all.sh\n\n";
    int merge_round = 1;
    while (true) {
        if (merge_round > 2) {
            std::cout << "merge completed" << std::endl;
            break;
        }
        std::cout << "start[ " << merge_round << " time]merge now? (Y/N)" << std::endl;
        char choose;
        std::cin >> choose;
        if (choose == 'Y' || choose == 'y') {
            client.start_merge(merge_round);
            ++merge_round;
        } else if (choose == 'N' || choose == 'n') {
            break;
        } else {
            std::cout << "Invalid input, please enter Y or N." << std::endl;
        }
    }
    return 0;
}
