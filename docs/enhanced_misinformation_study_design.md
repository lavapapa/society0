# 設計文檔：虛假信息研究的增強實驗設計

**作者:** Gemini

**日期:** 2025年9月20日

**狀態:** 提案

## 1. 概述

本文檔基於我們之前的討論，詳細闡述了對“虛假信息研究”示例進行功能增強和實驗流程改造的完整設計方案。此方案旨在將現有示例從一個簡單的技術演示，轉變為一個結構嚴謹、方法論上可靠的計算社會科學實驗。

核心研究問題：**在社交網絡環境中，不同的社區管理策略（例如，AI標註、管理員標註）在抑制虛假信息傳播和維持用戶信任方面，效果有何差異？**

## 2. 核心設計原則

- **關注點分離:** 實驗的各個邏輯部分（如刺激源、干預措施、測量工具）應在架構中解耦，成為獨立的、可配置的組件。
- **可重複性與可控性:** 實驗流程應是確定性的，所有隨機性（如干預目標的選取）都應是可控的（例如，通過設置隨機種子和參數）。
- **數據完整性:** 必須確保實驗數據的純淨性，測量過程不應反過來影響被測量的狀態。
- **代碼復用:** 最大限度地利用現有仿真引擎的成熟功能，如 `instruct` 認知循環、`behavior` 機制和事件系統。

## 3. 詳細設計方案

### 3.1. 智能體（Agent）設置

1.  **智能體擴充:**
    -   將普通 `social_user` 智能體的數量從3個擴充到40個。
    -   **動作:** 我將負責生成這40個智能體的 `persona` 字符串，確保其多樣性，覆蓋不同的背景、性格和數字素養，以形成一個更真實的模擬社會網絡。這些配置將被寫入 `config.yaml`。

2.  **引入“污染源”智能體:**
    -   在 `config.yaml` 中新增一個特殊的智能體，ID為 `source_of_lies`。
    -   **Persona:** 其 `persona` 將被精心設計，使其成為一個堅定、偏執且有說服力的虛假信息信源。
    -   **專屬行為:** 為該智能體創建一個新的、專屬的 `behavior`，命名為 `post_misinformation_if_ready`。
        -   **邏輯:** 該行為函數接收 `agent` 對象。其內部邏輯會檢查 `world.step >= 3`。
        -   如果條件滿足，它將調用 `agent.instruct` 或直接調用 `create_post` 動作，發布包含特定虛假信息內容和特定 **hashtag**（例如 `#illegal_immigrants_hunt_pets`）的帖子。
        -   這種方式將刺激源的邏輯封裝在獨立的 `behavior` 中，易於管理和修改。

### 3.2. 實驗流程與計劃（Schedule）調整

我們將重新設計 `schedule.yaml`，使其包含一個完整、清晰的實驗流程。每個仿真步驟（step）將依次執行以下節點：

1.  **`agent_interaction_phase` (節點1):**
    -   與當前類似，此節點選擇所有41個智能體，並讓他們使用帶有 `["social", "memory"]` 標籤的動作自由互動。

2.  **`misinformation_posting_phase` (節點2):**
    -   **Selector:** `type: by_id`, `agent_ids: ["source_of_lies"]`。
    -   **Operator:** `type: behavior`, `name: "post_misinformation_if_ready"`。
    -   此節點確保只有“污染源”智能體在特定時間執行其專屬的發帖行為。

3.  **`intervention_phase` (節點3):**
    -   **Selector:** `type: environment`。
    -   **Operator:** `type: custom`, `function: "apply_intervention_tags"`。這是一個需要新註冊的環境規則（env rule）。
    -   **邏輯:** `apply_intervention_tags` 函數將：
        a. 遍歷環境中的所有帖子。
        b. 識別出帖子內容包含特定 hashtag（如 `#illegal_immigrants_hunt_pets`）的帖子。
        c. 根據 `schedule.yaml` 中為此操作符配置的參數（如 `intervention_rate: 0.5`），隨機選擇一部分符合條件的帖子。
        d. **冪等性檢查:** 檢查帖子的 `special_tags` 列表，如果干預標籤（如 `"flagged_by_ai"`）**尚未存在**，則將其添加。這可以防止重複標記。

4.  **`questionnaire_phase` (節點4):**
    -   **Operator 1 (`interview_op`):**
        -   `type: interview` (使用我們新設計的訪談操作符)。
        -   `question:` 將包含 `questionnaire.md` 中所有三個維度的所有問題。
        -   `output_schema:` 將被定義為一個大的JSON結構，包含所有問題的答案字段（如 `tech_q1`, `system_q1` 等）。
    -   **Operator 2 (`calculation_op`):**
        -   `type: behavior`, `name: "calculate_and_save_trust"`。
        -   `input_mapping:` 將 `interview_op` 的整個 `structured_output` 映射到 `calculation_op` 的 `survey_results` 參數。
        -   **邏輯:** `calculate_and_save_trust` 這個 `behavior` 函數將負責計算每個維度的平均分，並將最終結果（如 `avg_tech_trust`）寫入 `agent.properties['digital_trust']`。

5.  **`measurement_phase` (節點5):**
    -   此節點與當前實現類似，但現在只負責最終的全局指標聚合。
    -   **Converter:** 使用 JMESPath 從所有智能體的 `properties.digital_trust` 中讀取已經計算好的維度得分，並計算整個群體的平均值。

### 3.3. 環境（Environment）與數據模型修改

這是本次增強中最具侵入性但至關重要的部分。

-   **文件:** `src/simengine/env/social_network/models.py` (或帖子類的定義文件)
-   **修改:** `Post` 數據類需要增加兩個字段：
    -   `special_tags: List[str] = field(default_factory=list)`
    -   `view_count: int = 0`

-   **文件:** `src/simengine/env/social_network/env.py` (或 `SocialNetworkEnv` 的實現文件)
-   **修改:** `get_recommended_feed` FoV 函數需要進行如下修改：
    1.  **增加瀏覽計數:** 每當一個帖子被選中並即將被包含在返回給用戶的 feed 中時，必須遞增其 `view_count` 屬性：`world.environment_data.state['posts'][post_id]['view_count'] += 1`。
    2.  **展示干預標籤:** 在為用戶格式化帖子文本時，檢查 `post.special_tags` 列表。如果列表不為空，將一個格式化的警告信息（例如 `"\n---此消息被AI助手標記為潛在虛假信息---"`）附加到帖子內容的末尾。這確保了干預措施能被智能體感知到。

## 4. 總結

該設計方案全面地響應了新的研究需求。通過引入專門的污染源智能體、設計多階段的仿真流程、實現一個冪等的干預規則以及改造核心數據模型和FoV函數，我們可以構建一個功能完備、邏輯嚴謹的虛假信息實驗平台。該平台不僅能模擬信息的傳播和干預，還能通過新設計的 `interview` 機制，可靠地測量出關鍵的因變量——多維度數字信任，同時確保了測量過程的純淨性。
