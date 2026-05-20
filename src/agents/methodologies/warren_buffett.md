# ウォーレン・バフェット投資手法ナレッジベース

> **目的**: 本ドキュメントはClaudeエージェントが日本株に対してバフェット流の投資判断を下す前に読み込む知識ベースです。各セクションには具体的数値・歴史的事例・チェック項目を含み、再現可能な意思決定を支援します。
> **最終更新**: 2026-05-12

---

## 0. 全体哲学（Mental Model）

バフェットの投資哲学は **「企業を買う、株式を買うのではない」（Buy a business, not a stock）** に集約される。短期株価変動を予測する試みではなく、優れた事業を妥当な価格で取得し、長期保有することで複利の力を最大化する。チャーリー・マンガーとの共同フレームワークは「Four Filters（4つのフィルター）」として知られる。

> "It's far better to buy a wonderful company at a fair price than a fair company at a wonderful price." — Warren Buffett, 1989 Letter

---

## 1. 4つのフィルター（Four Filters Framework）

バフェットとマンガーは、株式または企業全体を取得する際に以下4つのフィルターを **順次** 適用する。

### Filter 1: 理解可能な事業（Understandable Business）

- **基準**: 自分の **能力の輪（Circle of Competence）** の内側にあるか。10年後の事業構造を合理的に予測できるか。
- **テスト**: 「この会社のビジネスモデルを家族や友人に5分で説明できるか」「10年後にこの製品・サービスが必要とされているか」
- **具体例**:
  - **OK**: コカ・コーラ（清涼飲料）、シーズキャンディーズ（チョコレート小売）、ガイコ（自動車保険）
  - **NG（バフェット本人が長年回避）**: 半導体製造装置、創薬バイオ、暗号資産関連
  - **2016年の例外**: AppleはTed Weschler主導で投資。バフェットはこれを「テック株」ではなく「消費財ブランド+エコシステム」として分類した。

### Filter 2: 持続的競争優位性（Durable Competitive Advantage / Moat）

- **基準**: 10年以上競合参入を防げる構造的優位性があるか。
- **詳細はセクション3「Economic Moat」参照**。
- **テスト**: 「もし1,000億円与えられて、この会社を打ち負かそうと挑戦したらどうなるか」（マンガーが用いる思考実験）

### Filter 3: 誠実で有能な経営陣（Honest and Competent Management）

- **資本配分能力**: 過去10年の利益をどう再投資したか。ROEを維持しながら成長できたか。
- **誠実性**: 株主向け書簡の率直さ、悪いニュースを隠さないか、自社株買い・配当・M&Aの判断履歴。
- **株式保有**: 経営陣が自社株を相当額保有しているか（インサイダー比率）。
- **具体例**:
  - **理想**: バークシャー・ハサウェイ自身、トム・マーフィー（Capital Cities/ABC）、Tim Cook（Apple、自社株買いで株主還元）
  - **失敗例**: Tesco（2014年に利益水増し発覚、バフェットは経営陣の信頼性を見誤った）

### Filter 4: 魅力的な価格（Attractive / Sensible Price）

- 上記3つを満たした **後** に初めて検討する。質の確認なしに「割安」だけで買うのは **バリュートラップ** の典型。
- 詳細はセクション2「Valuation」参照。

---

## 2. バリュエーション手法（Valuation Methodology）

### 2.1 オーナー・アーニングス（Owner Earnings）

バフェットが1986年の株主書簡で定義した、GAAP純利益よりも経済実態を反映する指標。

```
オーナー・アーニングス
= 報告純利益（Reported Earnings）
+ 減価償却・償却費（D&A）
+ その他の非現金費用
− 維持的設備投資（Maintenance CapEx）
− 必要な運転資本増加（ΔWorking Capital）
```

| 項目 | 算出のヒント |
|---|---|
| **維持的CapEx** | 簡易法では「D&Aで近似」。より精緻には過去5年の総CapExから成長分を控除。 |
| **運転資本増加** | (売掛+棚卸)−買掛 の前期比増加分。事業拡大で必要になる分は控除。 |
| **非現金費用** | のれん償却、株式報酬、繰延税金などを再評価。 |

### 2.2 内在価値（Intrinsic Value）の定義

> "Intrinsic value is the discounted value of the cash that can be taken out of a business during its remaining life." — Buffett, Owner's Manual

これは **将来オーナー・アーニングスの割引現在価値の総和**。実務上は10〜15年の予測 + ターミナル価値（永久成長モデル）。

### 2.3 割引率: 10年米国債利回り（バフェット流）

バフェットは **WACCを使わない**。代わりに **10年米国債利回り** をリスクフリーレートとして用い、優良企業ならリスクプレミアムを上乗せしないこともある。

- 米10年債が4.5%なら、割引率はおおむね **4.5〜10%**（事業の確実性に応じて）。
- 日本株に適用する場合、**日本10年国債利回り** を基準にする説と、グローバル投資家視点で米10年債を使う説がある。本ドキュメントでは **保守的に高い方を採用**（例: 4.5% vs 1.5%なら4.5%）。

### 2.4 安全マージン（Margin of Safety）

ベンジャミン・グレアム由来の概念。バフェットは **内在価値の30〜50%ディスカウント** で買うことを基本とする。

| 事業の質 | 推奨安全マージン |
|---|---|
| 超優良（コカ・コーラ級のモート） | 30%以上 |
| 優良（一般的なバフェット銘柄） | 40%以上 |
| 普通（モートに不安） | 50%以上 |
| 不明確 | **買わない** |

### 2.5 「Approximately right vs. Precisely wrong」

バフェットはDCFの精密な計算に固執しない。

> "I'd rather be approximately right than precisely wrong." — Warren Buffett

実務上の意味:
- 5%刻みの感度分析で複数シナリオを作る
- 入力に少しの変更で結論が変わるなら、それは **明確な投資対象ではない**
- 計算結果が「ギリギリ割安」では不十分。**圧倒的に割安** でなければパス。

---

## 3. 経済的モート（経済堀 / Economic Moat）の種類

| モート種別 | 代表例 | 財務上のシグナル |
|---|---|---|
| **ブランド（Intangibles）** | Coca-Cola, See's Candies, Apple | 粗利率40%以上、安定的価格決定力、過去10年の値上げ実績 |
| **スイッチングコスト** | American Express, Moody's, Microsoft Office | 顧客解約率の低さ、契約更新率95%以上 |
| **ネットワーク効果** | AmEx決済網, Visa, MasterCard | 利用者×加盟店の双方向増加、限界利益率の上昇 |
| **コスト優位（Low-cost producer）** | GEICO（直販モデル）, Costco, GMO Internet | 業界最低の経費率、規模の経済 |
| **規制・無形資産** | Burlington Northern（鉄道）, 公益事業 | 認可・免許保有、参入規制、特許 |
| **効率的規模（Efficient Scale）** | 地域独占インフラ、空港 | 小さな市場で1〜2社しか経済性が成立しない |

### 3.1 財務諸表からモートを識別する手順

1. **ROE（過去10年）**: 15%以上が連続しているか。レバレッジに依存していないか確認（自己資本比率も併せて見る）。
2. **粗利率の安定性**: 過去10年で粗利率の変動係数（CV）が5%以下ならブランド/コスト優位の可能性。
3. **市場シェアの推移**: 業界内シェアが横ばいまたは上昇。下降傾向ならモート侵食の疑い。
4. **価格決定力**: 過去10年のインフレ率を上回る値上げを実施できているか。
5. **資本回転率**: 同業他社比較でROIC（投下資本利益率）が高い。

---

## 4. 定量スクリーニング基準（Quantitative Thresholds）

バフェットが明示的に語ったわけではないが、彼の投資先に共通する数値ベンチマーク:

| 指標 | 閾値 | 確認期間 |
|---|---|---|
| **ROE** | ≥ 15%（理想は20%以上） | 5〜10年平均、単年も15%未満なし |
| **粗利率（Gross Margin）** | 40%以上（消費財）、業界平均超 | 5年間安定または上昇 |
| **営業利益率** | 業界トップクラス | 5年間安定 |
| **Debt/Equity** | < 0.5（金融業除く） | 直近 |
| **インタレストカバレッジ** | > 5倍 | 直近 |
| **フリーキャッシュフロー** | 連続プラス | 過去10年 |
| **EPS成長率** | 安定的にプラス、変動係数低い | 過去10年 |
| **配当性向** | 安定または漸増 | 過去10年 |
| **自社株買い** | 株式数の横ばい〜減少 | 過去5年 |

> **重要**: 単一指標で判断しない。例えばROE 15%でもD/Eが2.0なら、レバレッジで嵩上げされた数値であり質的にNG。

---

## 5. 歴史的成功事例（Quantitative Details）

### 5.1 Coca-Cola（1988年〜現在も保有）

- **取得時**: 1988年、平均取得単価 **約$43.81/株**（分割調整後 $2.73）、PER **約15倍**、PBR約5倍
- **投資額**: 約$5.93億 → 1989年末までに約$10億
- **保有期間**: 37年以上（現在も保有）
- **論点**: ブランドモート、グローバル展開余地、配当の複利
- **教訓**: 1998年にPER45倍に達した時でも売らず、長期保有を貫いた。

### 5.2 GEICO（1976年/1996年）

- **1951年**: バフェット20歳、Lorimer Davidson副社長と4時間面談しビジネス理解
- **1976年**: 経営危機時に株価$2.125で50万株購入、追加で転換優先株$19.4M
- **1996年**: 残り49%株式を $23億で完全買収（バークシャー子会社化）
- **モート**: 直販モデルによる低コスト構造（コミッション削減）

### 5.3 American Express（1964年）

- **背景**: 1963年Salad Oil Scandal（サラダ油詐欺、子会社で$1.8億の損害）
- **取得**: 1964年初頭、Buffett Partnership が時価総額の約 **40%** を投じてAmEx株を購入（パートナーシップ史上最大の集中投資）
- **取得倍率**: 株価暴落で約半値
- **論点**: クレジットカードとトラベラーズチェックの本業は無傷、ブランドと利用者ネットワーク健在
- **結果**: 2年で株価3倍

### 5.4 Washington Post（1973年）

- **取得**: 1973年中頃、$10.6M投資
- **取得倍率**: バフェット推定の内在価値 $400〜500M に対し時価総額 **$100M** = **約25%の値段**（75%ディスカウント）
- **結果**: 1985年末で$221M、最終的に保有期間で数十倍
- **論点**: 地域独占新聞（当時のモート）、Katharine Graham の経営信頼

### 5.5 Apple（2016年〜）

- **取得**: 2016年Q1にTed Weschler主導で約$1B、その後Buffett本人主導で大幅増額
- **取得時PER**: 約10〜12倍（市場が成熟期入りを織り込み割安だった）
- **論点**: バフェットは「テック企業ではなく **消費財ブランド+エコシステム** 」と再分類。iPhoneユーザーの粘着性（switching cost）、Tim Cookの自社株買い還元姿勢を評価
- **教訓**: 「テック株は買わない」ルールはドグマではなく **能力の輪の問題**。理解可能な範囲なら例外も可。

### 5.6 日本5大商社（2020年〜）

- **取得開始**: 2020年8月（Buffett90歳の誕生日に発表）、約12ヶ月で取得
- **対象**: 伊藤忠商事、三菱商事、三井物産、丸紅、住友商事
- **取得時バリュエーション**（2020年時点参考値）:
  - PBR **0.7〜1.0倍**（多くが解散価値以下）
  - 配当利回り **約5%**
  - PER **6〜8倍**
- **資金調達**: 円建社債発行（クーポン約0.5〜1.0%）→ 配当利回り5%との **イールドスプレッド4%+** を確保
- **論点**:
  1. ディープバリュー（PBR1倍割れ）
  2. 多角化された資源・商品・金融エクスポージャー（ミニ・バークシャー）
  3. 円建借入による為替ヘッジ済み
  4. ガバナンス改革で株主還元加速
- **拡大**: 2025年時点で各社 **8.5〜9.8%** まで増加

### 5.7 Bank of America（2011年優先株ディール）

- **構造**:
  - $50億で累積永久優先株（年6%配当）
  - + 10年間 $7.14で7億株を購入できるワラント
- **背景**: サブプライム後の信用懸念、CEO Brian Moynihanからの要請
- **結果**: 2017年にワラント行使、$120億+の利益
- **教訓**: 危機時の優良企業への流動性供給ディールは、優先株+ワラントで下方リスク限定+アップサイド享受

---

## 6. 失敗事例と教訓（Notable Failures）

### 6.1 Dexter Shoe（1993年）

- **誤り**: 自国生産靴メーカーを **バークシャー株** で買収。海外（中国）競争を見抜けず、事業価値ゼロに。
- **被害額**: 機会費用込みで **約$35〜57億**（バフェット自身「Guinness World Recordsに値する」と発言）
- **教訓**: ① 持続的優位性の誤判定、② **自社株を対価にしてはいけない**（バフェット自身が後悔を明言）

### 6.2 Tesco（2006〜2014年）

- **誤り**: 英スーパー大手で当初成功、しかし **経営陣の利益水増し** が発覚。すぐに売却しなかった「Thumb-sucking（おしゃぶり）」で損失拡大。
- **被害額**: 約$4.4億
- **教訓**: 経営陣の誠実性に少しでも疑念が生じたら **即時行動**

### 6.3 IBM（2011〜2018年）

- **誤り**: 「ハイテクは買わない」ルールを破ったが、クラウド移行（AWS, Azure）への対応失敗を見抜けず
- **教訓**: 能力の輪の外で投資すると、ビジネスモデル変化を察知できない

### 6.4 Salomon Brothers（1987〜1991年）

- **背景**: 1987年に優先株で$7億投資。1991年にトレーダーのMozerによる国債入札不正発覚→バフェット自身がChairmanに就任
- **教訓**:
  - 投資銀行の **レバレッジ文化とリスク管理の脆さ**
  - 悪いニュースは即座に開示しないと致命的になる（"Lose money for the firm, and I will be understanding. Lose a shred of reputation, and I will be ruthless."）

### 6.5 ConocoPhillips（2008年）

- **誤り**: 原油$140超のピーク時に大量取得
- **教訓**: コモディティ・サイクル銘柄を高値で買うとモートも何も助けにならない

### 6.6 Lehman Brothers（投資見送り）

- **経緯**: 2008年Lehmanからの投資要請を断る
- **教訓**: レバレッジ文化と不透明なデリバティブ簿外負債への警戒。**「Don't lose money」**

---

## 7. 意思決定チェックリスト（Step-by-Step for Claude Agent）

投資判断前に **順次** 評価する。1つでもNGなら却下。

### Step 1: 理解可能性（Filter 1）
- [ ] 事業モデルを3文で説明できるか
- [ ] 10年後にこの会社が存続している確信があるか
- [ ] 自分の能力の輪の内側にあるか

### Step 2: モート（Filter 2）
- [ ] モートの種類を1つ以上特定できるか（ブランド/スイッチング/ネットワーク/コスト/規制）
- [ ] そのモートは過去10年で侵食されていないか
- [ ] 今後10年でディスラプションのリスクはないか

### Step 3: 定量チェック（Quality Screen）
- [ ] ROE 過去5年平均 ≥ 15%、最低年も ≥ 10%
- [ ] 粗利率 安定または上昇トレンド
- [ ] Debt/Equity < 0.5（金融業除く）
- [ ] フリーキャッシュフロー 過去10年全て正
- [ ] EPS変動係数（CV）< 30%

### Step 4: 経営陣（Filter 3）
- [ ] 過去5年の資本配分（自社株買い・配当・M&A）が合理的か
- [ ] 株主向け書簡・決算説明会で悪い話も率直に語っているか
- [ ] インサイダー持株比率が高いか、または増加傾向か
- [ ] 過去に不祥事・利益操作の履歴がないか

### Step 5: バリュエーション（Filter 4）
- [ ] オーナー・アーニングスを過去5年で算出
- [ ] 内在価値を控えめなシナリオで計算（成長率・割引率は保守的に）
- [ ] 現在の株価が内在価値の70%以下（30%以上の安全マージン）
- [ ] 配当利回り > 10年国債利回り（理想的には2倍以上）

### Step 6: ポジションサイジング
- [ ] 高確信銘柄なら20%超の集中も可（バフェットは過去にAmExで40%集中）
- [ ] 中確信なら5〜10%
- [ ] 低確信なら買わない（「Pass」が最良の選択肢）
- [ ] **レバレッジは使わない**

---

## 8. 日本株への適用（Adaptation to Japanese Equities）

### 8.1 なぜバフェットは2020年に5大商社を買ったか

| 要素 | 詳細 |
|---|---|
| **超低PBR** | 0.7〜1.0倍、解散価値以下 |
| **5%の配当利回り** | 米国優良株を上回る |
| **円建社債での通貨ヘッジ** | クーポン1%未満 vs 配当5% = リスクフリースプレッド |
| **多角化** | 各商社が資源・食料・繊維・金融・インフラを横断（ミニ・バークシャー） |
| **ガバナンス改革** | 自社株買い・親子上場解消・ROE目標設定 |
| **能力の輪** | 「コングロマリット」の評価はバフェットの得意分野 |

### 8.2 日本特有の構造的考慮事項

#### マイナス要因（伝統的）
- **クロスシェアホールディング**: 株式持ち合いで浮動株比率が低く、ROE圧迫要因
- **歴史的低ROE**: 過剰内部留保、現金保有過多、PBR1倍割れ常態化
- **ESG/ガバナンス文化**: 米国型エクイティカルチャーが弱い
- **円相場リスク**: 海外投資家視点では為替変動が長期リターンを希薄化

#### プラス要因（2014年以降の変化）
- **2014年 スチュワードシップ・コード**
- **2015年 コーポレートガバナンス・コード**
- **2023年 東証「PBR1倍割れ改善要請」**
- 結果: 自社株買い増加、政策保有株削減、社外取締役導入加速

### 8.3 バフェット流が機能しやすい日本のセクター

| セクター | 理由 | 候補例 |
|---|---|---|
| **食品・飲料** | ブランドモート、安定キャッシュフロー、必需品需要 | キッコーマン、味の素、サントリーBF |
| **生活必需品/化粧品** | リピート購買、価格決定力 | 花王、ライオン、資生堂 |
| **総合商社** | バフェット実証済み、PBR割安、配当良好 | 5大商社 |
| **金融（地銀以外）** | 規制モート、長期顧客関係 | メガバンク（要注意）、東京海上 |
| **鉄道・公益** | 規制独占、安定配当 | JR東日本、東京ガス |
| **ニッチ製造業** | 世界シェア、スイッチングコスト | キーエンス、SMC、信越化学 |

### 8.4 バフェット流が機能しにくい日本のセクター

- **半導体・電機**: サイクル激しい、技術ディスラプション
- **バイオ・創薬**: 研究開発リスク、能力の輪外
- **不動産・建設**: 景気循環、レバレッジ高
- **小売・外食**: 競争過多、参入障壁低
- **小型成長株（マザーズ/グロース）**: 実績不足、ボラティリティ高

### 8.5 日本株固有の確認項目

- **政策保有株比率**（純資産対比 < 20%が望ましい）
- **親子上場の解消有無**
- **配当性向 + 自社株買い** = 総還元性向 50%以上
- **ROE改善計画の有無**（中期経営計画に明記）
- **海外売上比率**（円安/円高耐性）

---

## 9. 誤適用の警告（Common Misapplications）

エージェントが陥りやすい誤りに対する明示的警告:

### 9.1 「安いからバフェット流」の誤り
- 低PER・低PBR **単独** ではバフェット流ではない。グレアム流の「Cigar Butt」投資は若いバフェットの戦略であり、現在のバフェットは **質を最優先**。
- **バリュートラップの典型**: 衰退業界・赤字常態・モート消失企業

### 9.2 質を犠牲にした安値の罠
- 「Fair price for a wonderful business > wonderful price for a fair business」
- 安全マージンは **質の確認後** にのみ意味を持つ

### 9.3 短期トレードへの誤用
- バフェット流は **保有期間10年以上** を前提
- 四半期決算で売買するのはバフェット流ではない
- "Our favorite holding period is forever."

### 9.4 レバレッジ使用の禁忌
- バフェットは個人投資でレバレッジを使わない
- バークシャー本体も保険フロート（実質ゼロコスト負債）以外のレバレッジは抑制
- 「To make money they didn't have, they risked what they did have. Foolish.」（LTCM評）

### 9.5 模倣の罠（Coat-tailing）
- 13Fで「バフェットが買った」だけで追随しない
- 取得時の **価格・モート・能力の輪** を自分で再評価する

### 9.6 マクロ予測への依存
- バフェットはマクロ予測（金利・為替・GDP）で売買しない
- ボトムアップの個別企業分析が中心

### 9.7 「次のApple」探しの罠
- バフェットのAppleは **既に消費財化した成熟企業** だった、初期成長株ではない
- 「次のApple」を初期段階で当てる試みはバフェット流から逸脱

---

## 10. 実務運用上の追加ガイダンス（Operational Notes）

### 10.1 ポジションサイジング
- 高確信: 20%以上の集中も許容（バフェットはAmExで40%）
- 中確信: 5〜10%
- 試し玉: 2〜3%
- 1銘柄に賭けすぎる場合は確信レベルを再評価

### 10.2 売却判断
バフェットは原則として売らないが、以下の場合は売却:
1. モートが消失したと判断した時（IBM、航空株）
2. 経営陣の誠実性に疑念（Tesco）
3. **当初の投資仮説が崩れた時**（株価ではなく仮説）
4. より魅力的な投資機会が明確に存在する時

### 10.3 待つことの価値
- 「Be greedy when others are fearful」
- 投資機会がない時は **現金保有が正解**（バークシャーは$1,000億超を現金保有することもある）
- 「Sit on your ass investing」（マンガー）

### 10.4 記録と振り返り
- 投資理由を **書き残す**（後で検証可能にする）
- 失敗を恥じず分析する（Buffettは株主書簡で失敗を率直に開示）

---

## 11. 出典・参考文献（Sources）

### 一次資料
- [Berkshire Hathaway Annual Letters (1965-2024)](https://www.berkshirehathaway.com/letters/letters.html) — 特に1986年（Owner Earnings定義）、1996年（Owner's Manual）、2020-2024年（日本商社）
- [Berkshire Hathaway Owner's Manual](https://www.berkshirehathaway.com/ownman.pdf)

### 書籍
- Benjamin Graham, *The Intelligent Investor* (1949, rev. 1973) — Margin of Safety概念の起源
- Roger Lowenstein, *Buffett: The Making of an American Capitalist* (1995)
- Alice Schroeder, *The Snowball: Warren Buffett and the Business of Life* (2008)
- Bud Labitan, *The Four Filters Invention of Warren Buffett and Charlie Munger* (2008)
- Lawrence Cunningham (ed.), *The Essays of Warren Buffett: Lessons for Corporate America*

### 主要オンライン記事
- [Owner Earnings - Wikipedia](https://en.wikipedia.org/wiki/Owner_earnings)
- [Economic Moat - Wikipedia](https://en.wikipedia.org/wiki/Economic_moat)
- [Salad Oil Scandal - Wikipedia](https://en.wikipedia.org/wiki/Salad_oil_scandal)
- [How Warren Buffett Calculates Intrinsic Value - StableBread](https://stablebread.com/warren-buffett-intrinsic-value/)
- [Warren Buffett's Owner Earnings - StableBread](https://stablebread.com/warren-buffett-owners-earnings/)
- [Coca-Cola's Valuation, Warren Buffett's 1988 Purchase - GuruFocus](https://www.gurufocus.com/news/215892/cocacolas-valuation-warren-buffetts-1988-purchase)
- [Warren Buffett & The Washington Post - Max Olson](https://futureblind.com/p/warren-buffett-washington-post)
- [Berkshire Hathaway to Invest $5 Billion in Bank of America - Blackstone](https://www.blackstone.com/news/press/berkshire-hathaway-to-invest-5-billion-in-bank-of-america/)
- [Why Buffett's Japanese Trading House Picks Have Room to Rise - Morningstar](https://www.morningstar.com/stocks/why-buffetts-japanese-trading-house-picks-have-room-rise-20-or-more)
- [Warren Buffett's Japan trade - Nikkei Asia](https://asia.nikkei.com/business/business-spotlight/warren-buffett-s-japan-trade-the-changing-world-of-sogo-shosha)
- [Warren Buffett explains why he bought 5 Japanese trading houses - CNBC](https://www.cnbc.com/2023/04/12/warren-buffett-why-he-bought-5-japanese-trading-houses.html)
- [Warren Buffett's wild ride at Salomon - Fortune](https://fortune.com/article/warren-buffett-salomon/)
- [Warren Buffett changed his investing strategy with American Express - Fortune](https://fortune.com/2024/09/22/warren-buffett-investing-strategy-american-express-stock-scandal/)
- [Apple remains Buffett's biggest holding - CNBC](https://www.cnbc.com/2024/05/03/apple-is-buffetts-biggest-stock-but-moat-thesis-faces-questions.html)
- [Warren Buffett's failures: 15 investing mistakes - CNBC](https://www.cnbc.com/2017/12/15/warren-buffetts-failures-15-investing-mistakes-he-regrets.html)
- [Have Corporate Reforms in Japan Unlocked Shareholder Value? - MSCI](https://www.msci.com/research-and-insights/blog-post/have-corporate-reforms-in-japan-unlocked-shareholder-value)
- [Japan's Coming Wave of Reform - Harvard CorpGov](https://corpgov.law.harvard.edu/2022/01/11/japans-coming-wave-of-reform/)
- [The Four Filters of Warren Buffett and Charlie Munger - Seeking Alpha](https://seekingalpha.com/article/69566-the-four-filters-of-warren-buffett-and-charlie-munger)

### 動画・インタビュー
- CNBC Buffett & Munger interviews (1994-2024)
- Berkshire Hathaway Annual Meeting Q&A (1994-2024)

---

## 12. 最終チェック（Agent Pre-Decision Final Gate）

**投資実行前に必ず以下を自問**:

1. この投資判断は4つのフィルター **すべて** を通過したか?
2. 私は能力の輪の **内側** にいるか?
3. 内在価値計算で **最低30%の安全マージン** を確保しているか?
4. 経営陣の誠実性に **少しでも疑念** はないか?
5. 過去10年の財務指標は **すべて** 質的基準を満たすか?
6. 株主に対し、この投資理由を書面で説明できるか?
7. 翌日から10年間、株価を見られなくても安心して保有できるか?

**7つすべてYESでなければ、Pass（買わない）が正解。**

> "The stock market is designed to transfer money from the active to the patient." — Warren Buffett
