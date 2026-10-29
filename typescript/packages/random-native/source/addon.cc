#include <node_api.h>
#include <Random123/philox.h>
#include <random>
#include <vector>
#include <stdexcept>
#include <cmath>
#include <cstdint>

static void check(napi_status s) { if (s != napi_ok) throw std::runtime_error("RANDOM_NATIVE_ARGUMENT"); }
static napi_value output(napi_env env, const r123::Philox4x32_R<10>::ctr_type& words) {
    napi_value result; check(napi_create_array_with_length(env, 4, &result));
    for (uint32_t i=0; i<4; ++i) { napi_value v; check(napi_create_uint32(env, words[i], &v)); check(napi_set_element(env,result,i,v)); }
    return result;
}
static uint32_t word(napi_env env, napi_value array, uint32_t i) {
    napi_value v; double n; check(napi_get_element(env,array,i,&v)); check(napi_get_value_double(env,v,&n));
    if (!std::isfinite(n) || n<0 || n>4294967295.0 || std::floor(n)!=n) throw std::runtime_error("RANDOM_UINT32");
    return static_cast<uint32_t>(n);
}
static void arrayLength(napi_env env, napi_value array, uint32_t size) {
    bool is; uint32_t n; check(napi_is_array(env,array,&is)); if(!is) throw std::runtime_error("RANDOM_ARRAY");
    check(napi_get_array_length(env,array,&n)); if(n!=size) throw std::runtime_error("RANDOM_ARRAY_LENGTH");
}
static napi_value philox(napi_env env, napi_callback_info info) {
    try {
        size_t n=2; napi_value args[2]; check(napi_get_cb_info(env,info,&n,args,nullptr,nullptr));
        if(n!=2) throw std::runtime_error("RANDOM_ARGUMENTS");
        arrayLength(env,args[0],4); arrayLength(env,args[1],2);
        r123::Philox4x32_R<10>::ctr_type c; r123::Philox4x32_R<10>::key_type k;
        for(uint32_t i=0;i<4;++i)c[i]=word(env,args[0],i);
        for(uint32_t i=0;i<2;++i)k[i]=word(env,args[1],i);
        return output(env,r123::Philox4x32_R<10>()(c,k));
    } catch(const std::exception& e) { napi_throw_type_error(env,nullptr,e.what()); return nullptr; }
}
static napi_value address(napi_env env, napi_callback_info info) {
    try {
        size_t n=2; napi_value args[2]; check(napi_get_cb_info(env,info,&n,args,nullptr,nullptr));
        if(n!=2) throw std::runtime_error("RANDOM_ARGUMENTS");
        bool buffer; check(napi_is_buffer(env,args[0],&buffer)); if(!buffer) throw std::runtime_error("RANDOM_BUFFER");
        void* data; size_t length; check(napi_get_buffer_info(env,args[0],&data,&length));
        uint64_t index; bool lossless; check(napi_get_value_bigint_uint64(env,args[1],&index,&lossless));
        if(!lossless) throw std::runtime_error("RANDOM_INDEX_RANGE");
        const auto* bytes=static_cast<const uint8_t*>(data);
        std::seed_seq mapping(bytes,bytes+length); uint32_t mapped[4]; mapping.generate(mapped,mapped+4);
        r123::Philox4x32_R<10>::key_type key={{mapped[0],mapped[1]}};
        r123::Philox4x32_R<10>::ctr_type counter={{uint32_t(index),uint32_t(index>>32),mapped[2],mapped[3]}};
        return output(env,r123::Philox4x32_R<10>()(counter,key));
    } catch(const std::exception& e) { napi_throw_type_error(env,nullptr,e.what()); return nullptr; }
}
static napi_value init(napi_env env,napi_value exports) {
    napi_property_descriptor props[]={{"philox",nullptr,philox,nullptr,nullptr,nullptr,napi_default,nullptr},{"address",nullptr,address,nullptr,nullptr,nullptr,napi_default,nullptr}};
    if(napi_define_properties(env,exports,2,props)!=napi_ok)return nullptr;
    return exports;
}
NAPI_MODULE(NODE_GYP_MODULE_NAME,init)
