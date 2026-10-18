// T01099（M4.4）TSF 原型第一步：TSF 客户端链路验证探针
//
// 验证目标（对应任务"先产出原型验证 TSF 候选 UI 与注入链路"）：
//   [1] TSF COM 体系可用性：CoCreateInstance(CLSID_TF_ThreadMgr) —— 一切 TSF 编程的入口
//   [2] ITfThreadMgr::Activate：取得 TfClientId，任何 TSF 事件/文本操作的前置
//   [3] ITfInputProcessorProfiles：输入法处理器（TIP）管理接口可用
//   [4] 枚举系统全部输入法 —— 证明可程序化发现键盘/TIP
//   [5] GetLanguageProfileDescription —— 读取 TIP 元数据（未来候选条/切换 UI 的数据源）
//
// 结论口径：以上为 TSF IME 完整开发（实现 ITfTextInputProcessor 的 DLL）的客户端侧前置；
// 若本探针全部通过，说明本机工具链 + SDK 支持 TSF 开发，IME DLL 骨架可进入实施评估。
//
// 编译：cl /EHsc /W3 tsf_probe.cpp ole32.lib user32.lib
#include <windows.h>
#include <msctf.h>
#include <io.h>
#include <fcntl.h>
#include <stdio.h>

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "oleaut32.lib")

static void dump_tips(ITfInputProcessorProfiles* profs, ITfInputProcessorProfileMgr* profMgr,
                      LANGID* langs, ULONG nLang, int* pTotal) {
    for (ULONG i = 0; i < nLang; ++i) {
        IEnumTfInputProcessorProfiles* en = nullptr;
        if (FAILED(profMgr->EnumProfiles(langs[i], &en)) || !en) continue;
        TF_INPUTPROCESSORPROFILE p{};
        ULONG fetched = 0;
        while (en->Next(1, &p, &fetched) == S_OK && fetched == 1) {
            ++(*pTotal);
            if (p.clsid != GUID_NULL) {  // clsid 非空 = 真正的 TSF TIP（非纯键盘布局）
                BSTR desc = nullptr;
                if (SUCCEEDED(profs->GetLanguageProfileDescription(p.clsid, p.langid, p.guidProfile, &desc))
                    && desc) {
                    wprintf(L"    TIP langid=0x%04x  %s\n", p.langid, desc);
                    SysFreeString(desc);
                }
            }
        }
        en->Release();
    }
}

int wmain() {
    _setmode(_fileno(stdout), _O_U16TEXT);

    HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    wprintf(L"[0] CoInitializeEx: 0x%08x\n", hr);
    if (FAILED(hr)) return 1;

    ITfThreadMgr* mgr = nullptr;
    hr = CoCreateInstance(CLSID_TF_ThreadMgr, nullptr, CLSCTX_INPROC_SERVER,
                          IID_ITfThreadMgr, (void**)&mgr);
    wprintf(L"[1] ITfThreadMgr CoCreate: 0x%08x\n", hr);
    if (FAILED(hr)) return 1;

    TfClientId cid = 0;
    hr = mgr->Activate(&cid);
    wprintf(L"[2] ThreadMgr Activate: 0x%08x (clientId=%d)\n", hr, cid);
    if (FAILED(hr)) { mgr->Release(); return 1; }

    ITfInputProcessorProfiles* profs = nullptr;
    hr = CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER,
                          IID_ITfInputProcessorProfiles, (void**)&profs);
    wprintf(L"[3] InputProcessorProfiles CoCreate: 0x%08x\n", hr);
    if (FAILED(hr)) { mgr->Deactivate(); mgr->Release(); return 1; }

    ITfInputProcessorProfileMgr* profMgr = nullptr;
    hr = profs->QueryInterface(IID_ITfInputProcessorProfileMgr, (void**)&profMgr);
    wprintf(L"[3.5] QueryInterface(ITfInputProcessorProfileMgr): 0x%08x\n", hr);

    LANGID* langs = nullptr;
    ULONG nLang = 0;
    hr = profs->GetLanguageList(&langs, &nLang);
    wprintf(L"[4] GetLanguageList: 0x%08x (languages=%u)\n", hr, nLang);

    int total = 0;
    if (SUCCEEDED(hr) && SUCCEEDED(profMgr->QueryInterface(IID_ITfInputProcessorProfileMgr, (void**)&profMgr))) {
        // （profMgr 已在上面 QI；此处直接使用）
    }
    if (SUCCEEDED(hr)) dump_tips(profs, profMgr, langs, nLang, &total);
    wprintf(L"[5] TIP 总数（含键盘布局）=%d\n", total);
    wprintf(L"==== TSF 客户端链路验证%s ====\n", SUCCEEDED(hr) ? L"通过" : L"失败");

    CoTaskMemFree(langs);
    if (profMgr) profMgr->Release();
    profs->Release();
    mgr->Deactivate();
    mgr->Release();
    CoUninitialize();
    return 0;
}
