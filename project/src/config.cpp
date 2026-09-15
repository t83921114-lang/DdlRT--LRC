#include "config.h"
#include "tinyxml2.h"
#include <cassert>
#include <limits>

namespace ECProject
{
  Config *Config::instance = nullptr;

  Config::Config(const std::string &configPath)
  {
    loadConfig(configPath);
    printConfigs();
    validateConfig();
  }

  void Config::validateConfig() const
  {
    assert(BlockSize % UnitSize == 0 && "Error: BlockSize must be divisible by UnitSize");
    assert((AppendMode == "REP_MODE" || AppendMode == "UNILRC_MODE" || AppendMode == "CACHED_MODE" || AppendMode == "EQUIOX_MODE") && "Error: AppendMode must be REP_MODE, UNILRC_MODE, or CACHED_MODE");
    assert((CodeType == "UniLRC" || CodeType == "AzureLRC" || CodeType == "OptimalLRC" || CodeType == "UniformLRC" || CodeType == "RS" || CodeType == "DdlRT_LRC" || CodeType == "SRS" || CodeType == "ERS") && "Error: unsupported CodeType");
    assert(DatanodeNumPerCluster > 0 && "Error: DatanodeNumPerCluster must be greater than 0");
    assert(ClusterNum > 0 && "Error: ClusterNum must be greater than 0");
    if (CodeType == "UniLRC")
    {
      assert(DatanodeNumPerCluster > n / z && "Error: DatanodeNumPerCluster must be greater than n / z");
      assert(ClusterNum > z && "Error: ClusterNum must be greater than z");
    }
    if (CodeType == "AzureLRC")
    {
      assert(DatanodeNumPerCluster > k / z + 1 && "Error: DatanodeNumPerCluster must be greater than k / z + 1");
      assert(ClusterNum > z + 1 && "Error: ClusterNum must be greater than z + 1");
    }
    if (CodeType == "SRS" || CodeType == "ERS")
    {
      assert(k > 0 && r >= 1 && z >= 1 && k % z == 0 && "Error: SRS/ERS requires k divisible by z");
      const int capacity = r + 1;
      const int data_per_group = k / z;
      const int full_parts = data_per_group / capacity;
      const int tail_load = data_per_group % capacity;
      const int parts = full_parts + (tail_load > 0 ? 1 : 0);
      const int required_racks = 1 + z * parts + z * full_parts;
      assert(DatanodeNumPerCluster >= std::max(r + z, capacity) && "Error: SRS/ERS rack capacity is insufficient");
      assert((tail_load == 0 || DatanodeNumPerCluster >= 2 * tail_load) && "Error: SRS/ERS shared tail nodes are insufficient");
      assert(ClusterNum >= required_racks && "Error: SRS/ERS pair placement needs more racks");
    }
    if (CodeType == "OptimalLRC")
    {
      assert(DatanodeNumPerCluster > r + 1 && "Error: DatanodeNumPerCluster must be greater than r + 1");
      assert(ClusterNum > std::ceil(1.0 * k / z / (r + 1)) * z + 1 && "Error: ClusterNum must be greater than std::ceil(1.0 * k / z / (r + 1)) * z + 1");
    }
    if (CodeType == "UniformLRC")
    {
      assert(DatanodeNumPerCluster > r && "Error: DatanodeNumPerCluster must be greater than r");
      assert(ClusterNum > ((((k + r) / z + 1) / (r + 1) + (bool)(((k + r) / z + 1) % (r + 1))) * ((k + r) % z)) + (((k + r) / z) / (r + 1) + (bool)(((k + r) / z) % (r + 1))) * (z - ((k + r) % z)) && "Error: ClusterNum must be greater than ((((k + r) / z + 1) / (r + 1) + (bool)(((k + r) / z + 1) % (r + 1))) * ((k + r) % z)) + (((k + r) / z) / (r + 1) + (bool)(((k + r) / z) % (r + 1))) * (z - ((k + r) % z))");
    }
    if (CodeType == "RS")
    {
      int n = k + r;

      assert(DatanodeNumPerCluster >= 1 && "Error: DatanodeNumPerCluster must be >= 1 for RS code");

      assert(ClusterNum * DatanodeNumPerCluster >= n && "Error: ClusterNum * DatanodeNumPerCluster must be >= (k + r) for RS code");
    }
    if (CodeType == "DdlRT_LRC")
    {
      assert(k > 0 && r >= 1 && z >= 1 && "Error: DdlRT_LRC requires k > 0, r >= 1, and z >= 1");
      assert(k % z == 0 && "Error: DdlRT_LRC requires k to be divisible by z");
      assert(ClusterNum == 17 && "Error: DdlRT_LRC currently requires exactly 17 clusters");
      assert(DatanodeNumPerCluster >= std::max(r + z, r + 1) && "Error: not enough datanodes for DdlRT_LRC rack capacity");
      assert(get_ddlrt_lrc_racks(0) <= ClusterNum && "Error: one DdlRT_LRC stripe does not fit in the configured clusters");
    }
  }

  Config *Config::getInstance(const std::string &configPath)
  {
    if (instance == nullptr)
    {
      instance = new Config(configPath);
    }
    return instance;
  }

  void Config::loadConfig(const std::string &configPath)
  {
    tinyxml2::XMLDocument doc;
    if (doc.LoadFile(configPath.c_str()) != tinyxml2::XML_SUCCESS)
    {
      std::cerr << "Failed to load config file: " << configPath << std::endl;
      return;
    }

    tinyxml2::XMLElement *root = doc.RootElement();
    if (root == nullptr)
    {
      std::cerr << "Invalid config file format" << std::endl;
      return;
    }

    if (auto elem = root->FirstChildElement("AlignedSize"))
      AlignedSize = std::stoi(elem->GetText());
    if (auto elem = root->FirstChildElement("UnitSize"))
      UnitSize = std::stoi(elem->GetText());
    if (auto elem = root->FirstChildElement("BlockSize"))
      BlockSize = std::stoi(elem->GetText());
    if (auto elem = root->FirstChildElement("z"))
      z = std::stoi(elem->GetText());
    if (auto elem = root->FirstChildElement("CodeType"))
      CodeType = std::string(elem->GetText());
    if (auto elem = root->FirstChildElement("BaselineSeed"))
      BaselineSeed = std::stoull(elem->GetText());
    if (CodeType == "UniLRC")
    {
      if (auto elem = root->FirstChildElement("alpha"))
        alpha = std::stoi(elem->GetText());
      k = alpha * z * z - alpha * z;
      r = alpha * z;
    }
    else if (CodeType == "RS")
    {
      this->z = 0;
      if (auto elem = root->FirstChildElement("k"))
        k = std::stoi(elem->GetText());
      if (auto elem = root->FirstChildElement("r"))
        r = std::stoi(elem->GetText());
    }
    else
    {
      if (auto elem = root->FirstChildElement("k"))
        k = std::stoi(elem->GetText());
      if (auto elem = root->FirstChildElement("r"))
        r = std::stoi(elem->GetText());
    }
    n = k + r + z;

    if (auto elem = root->FirstChildElement("DatanodeNumPerCluster"))
      DatanodeNumPerCluster = std::stoi(elem->GetText());
    if (auto elem = root->FirstChildElement("ClusterNum"))
      ClusterNum = std::stoi(elem->GetText());
    if (auto elem = root->FirstChildElement("CoordinatorIP"))
      CoordinatorIP = std::string(elem->GetText());
    if (auto elem = root->FirstChildElement("CoordinatorPort"))
      CoordinatorPort = std::stoi(elem->GetText());
    if (auto elem = root->FirstChildElement("AppendMode"))
      AppendMode = std::string(elem->GetText());
    if (CodeType == "DdlRT_LRC")
      init_ddlrt_lrc_merge_parameters();
    else
    {
      N = get_N(); // 获得N
      get_num_arry();
    }
  }

  void Config::printConfigs() const
  {
    std::cout << "Configuration Parameters:" << std::endl;
    std::cout << "  AlignedSize: " << AlignedSize << " bytes" << std::endl;
    std::cout << "  UnitSize: " << UnitSize << " bytes" << std::endl;
    std::cout << "  BlockSize: " << BlockSize << " bytes" << std::endl;
    std::cout << "  alpha: " << (int)alpha << std::endl;
    std::cout << "  z: " << (int)z << std::endl;
    std::cout << "  n: " << n << std::endl;
    std::cout << "  k: " << k << std::endl;
    std::cout << "  r: " << r << std::endl;
    std::cout << "  (n, k, r, z): (" << n << ", " << k << ", " << r << ", " << (int)z << ")" << std::endl;
    std::cout << "  DatanodeNumPerCluster: " << DatanodeNumPerCluster << " nodes/cluster" << std::endl;
    std::cout << "  ClusterNum: " << (int)ClusterNum << " clusters" << std::endl;
    std::cout << "  CoordinatorIP: " << CoordinatorIP << std::endl;
    std::cout << "  CoordinatorPort: " << CoordinatorPort << std::endl;
    std::cout << "  AppendMode: " << AppendMode << std::endl;
    std::cout << "  CodeType: " << CodeType << std::endl;
    std::cout << "  BaselineSeed: " << BaselineSeed << std::endl;
    if (CodeType == "DdlRT_LRC")
    {
      std::cout << "  DdlRT_LRC merge rounds: " << N << std::endl;
      std::cout << "  DdlRT_LRC S:";
      for (int value : ddlrt_lrc_s) std::cout << " " << value;
      std::cout << std::endl;
    }
  }
  int Config::get_ddlrt_lrc_racks(int level) const
  {
    if (level < 0 || k <= 0 || r < 0 || z <= 0 || k % z != 0) return ClusterNum + 1;
    const long long stripes = 1LL << level;
    const long long data_per_local_group = stripes * static_cast<long long>(k / z);
    const long long data_racks_per_group =
        (data_per_local_group + (r + 1) - 1) / (r + 1);
    const long long racks = data_racks_per_group * z + 1;
    return racks > std::numeric_limits<int>::max()
               ? std::numeric_limits<int>::max()
               : static_cast<int>(racks);
  }

  void Config::init_ddlrt_lrc_merge_parameters()
  {
    N = 0;
    num_arry.clear();
    ddlrt_lrc_s.clear();
    if (k <= 0 || r < 1 || z < 1 || k % z != 0 || ClusterNum <= 0) return;

    int previous_racks = get_ddlrt_lrc_racks(0);
    for (int level = 1; level < 31; ++level)
    {
      const int current_racks = get_ddlrt_lrc_racks(level);
      const int shared_racks = 2 * previous_racks - current_racks;
      if (current_racks > ClusterNum ||
          (shared_racks != 1 && shared_racks != 1 + z))
        break;
      ddlrt_lrc_s.push_back(shared_racks);
      N = level;
      previous_racks = current_racks;
    }
  }

  int Config::get_N()
  {
    // Find the maximum N such that ceil(((2^N * k) + r) / r) fits in ClusterNum.
    // If ClusterNum is too small, return 0 to avoid infinite loop.
    if (ClusterNum <= 0 || r <= 0) return 0;
    for (int N = 0; N < 32; N++)
    {
      int temp_num_1 = static_cast<int>(std::ceil(((std::pow(2.0, N) * k) + r) / static_cast<double>(r)));
      int temp_num_2 = static_cast<int>(std::ceil(((std::pow(2.0, N + 1) * k) + r) / static_cast<double>(r)));
      if (temp_num_1 <= ClusterNum && temp_num_2 > ClusterNum)
      {
        return N;
      }
    }
    return 0;
  }
  void Config::get_num_arry()
  {
    this->num_arry.clear();
    for (int i = 1; i <= N; i++)
    {
      int temp_num = (static_cast<int>(std::ceil((std::pow(2.0, i - 1) * k + r) / static_cast<double>(r))) * 2) -
                     static_cast<int>(std::ceil((std::pow(2.0, i) * k + r) / static_cast<double>(r)));
      int L = static_cast<int>(temp_num);
      this->num_arry.push_back(L);
    }
  }
}
