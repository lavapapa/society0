#include <Random123/philox.h>
#include <random>
#include <vector>
#include <string>
#include <iostream>
#include <iomanip>
#include <cstdint>
int main(int argc,char** argv) {
    if(argc!=7)return 2;
    const std::string domain="society0/random/address/v1";
    std::vector<uint32_t> bytes(domain.begin(),domain.end());bytes.push_back(0);
    for(int i=1;i<=5;i++){
        std::string hex=argv[i];if(hex.size()%2)return 3;
        uint64_t length=hex.size()/2;
        for(int b=0;b<8;b++)bytes.push_back(uint32_t((length>>(8*b))&255));
        for(size_t j=0;j<hex.size();j+=2)bytes.push_back(std::stoul(hex.substr(j,2),nullptr,16));
    }
    std::seed_seq seq(bytes.begin(),bytes.end());uint32_t v[4];seq.generate(v,v+4);
    uint64_t index=std::stoull(argv[6]);
    r123::Philox4x32_R<10>::ctr_type counter={{uint32_t(index&0xffffffff),uint32_t(index>>32),v[2],v[3]}};
    r123::Philox4x32_R<10>::key_type key={{v[0],v[1]}};
    auto out=r123::Philox4x32_R<10>()(counter,key);
    std::cout<<"{\"encoded\":[";
    for(size_t i=0;i<bytes.size();i++){if(i)std::cout<<",";std::cout<<bytes[i];}
    std::cout<<"],\"key\":["<<key[0]<<","<<key[1]<<"],\"counter\":["<<counter[0]<<","<<counter[1]<<","<<counter[2]<<","<<counter[3]<<"],\"uint32\":"<<out[0]<<",\"uniform\":";
    double uniform=(double(out[0]>>5)*67108864.0+double(out[1]>>6))/9007199254740992.0;
    std::cout<<std::setprecision(17)<<uniform<<"}\n";
}
