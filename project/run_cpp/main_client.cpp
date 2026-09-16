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
        } else if (!argument.empty() && argument[0] != '-' && !coordinator_from_cli) {
            // Preserve the legacy positional coordinator argument.
            coordinator_addr = argument;
            coordinator_from_cli = true;
        } else if (argument == "--help" || argument == "-h") {
            std::cout << "Usage: " << argv[0]
                      << " [--coordinator HOST:PORT] [--stripes COUNT]" << std::endl;
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
    else{
        std::cout << "Code type error" << std::endl;
        return -1;
    }
    double block_size = static_cast<double> (parameters[3]) / 1024 / 1024; //MB
    int n = k + r + z;


    
    size_t total_write_size = static_cast<size_t>(stripe_num * block_size * n); // MB, for calculating throughput
    std::cout << "Starting set stripe operation" << std::endl;
    std::chrono::high_resolution_clock::time_point set_start = std::chrono::high_resolution_clock::now();
    for(int i = 0; i < stripe_num; i++){
        client.set();
    }
    std::chrono::high_resolution_clock::time_point set_end = std::chrono::high_resolution_clock::now();
    std::cout << "Set stripe operation finished" << std::endl;
    std::cout << "Conducting experiments, please wait..." << std::endl;
    std::chrono::duration<double> set_time = std::chrono::duration_cast<std::chrono::duration<double>>(set_end - set_start);
    std::cout << "write time: " << set_time.count() << " seconds" << std::endl;
    std::cout << "write throughput: " << (static_cast<double> (total_write_size) / set_time.count() / 1024) << "MB/s" << std::endl;

    std::cout << "\n[Merge bandwidth] if you want to limit the bandwidth during merge, please execute the following commands:\n"
            << "  before merge please execute: sh limit_bandwidth.sh\n"
            << "  after merge please execute: sh unlimit_all.sh\n\n";
     int merge_round=1;

    while (true)
    {
        if(merge_round>2)
        {
            std::cout<<"merge completed"<<std::endl;
            break;
        }
        std::cout << "start[ "<<merge_round<<" time]merge now? (Y/N)" << std::endl;
        char choose;
        std::cin >> choose;
        if (choose == 'Y' || choose == 'y')
        {      
            client.start_merge(merge_round);
            ++merge_round;
        }
        else if (choose == 'N' || choose == 'n')
        {
            break;
        }
        else
        {
            std::cout << "Invalid input, please enter Y or N." << std::endl;
        }
    }
    //read test
    // int stripe_id_to_read = 0;
    // int start_block_id = stripe_id_to_read * n;
    // int end_block_id = start_block_id + k - 1;
    // std::cout << "reading one stripe once"<< std::endl;
    // std::chrono::high_resolution_clock::time_point t1 = std::chrono::high_resolution_clock::now();
    // client.get_blocks(start_block_id, end_block_id);
    // std::chrono::high_resolution_clock::time_point t2 = std::chrono::high_resolution_clock::now();
    // std::chrono::duration<double> time_span = std::chrono::duration_cast<std::chrono::duration<double>>(t2 - t1);
    // double elapsed_s = time_span.count();
    // int block_size_bytes = parameters[3];
    // int requested_blocks = end_block_id - start_block_id + 1;
    // double physical_tp_mib =
    //     static_cast<double>(requested_blocks) * block_size_bytes /
    //     elapsed_s / (1024.0 * 1024.0);
    // std::cout<<"read time: "<<elapsed_s<<" seconds"<<std::endl;
    // std::cout << "read rate: " << physical_tp_mib << "MB/s" << std::endl;
    return 0;
}
