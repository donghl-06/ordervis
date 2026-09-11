<template>
  <context-holder />
  <div class="ov-page" :class="{ 'ov-dark': darkMode }">
    <!-- ① 顶栏 第1行：标的选择 + 状态展示 -->
    <div class="ov-topbar">
      <div class="ov-topbar-row">
        <div class="ov-bar-group">
          <Select
            v-model:value="securityType"
            class="ov-select w-type"
            :options="securityTypeOptions"
            :allow-clear="false"
            @change="handleSecurityTypeChange"
          />
          <Select
            v-model:value="selectSym"
            show-search
            :placeholder="`选择${securityType === 'fund' ? '基金' : '股票'}代码`"
            class="ov-select w-sym"
            :options="filteredSymsData"
            :filter-option="symFilterOption"
            :allow-clear="false"
            @change="selectSymItem"
            @search="handleSymSearch"
            @blur="handleSymBlur"
            @focus="handleSymFocus"
            @input="handleSymInput"
          />
          <Select
            v-model:value="selectDate"
            show-search
            placeholder="选择日期"
            class="ov-select w-date"
            :options="datesData"
            :filter-option="dateFilterOption"
            :allow-clear="false"
            @change="selectDateItem"
            @search="handleDateSearch"
            @blur="handleDateBlur"
          />
          <Button
            type="primary"
            :disabled="!hasSymbol || !hasDate || loading"
            @click="getOrderbookData"
          >
            <Spin v-if="loading" size="small" style="margin-right: 8px" />
            {{ loading ? '加载中...' : '开始' }}
          </Button>
          <GlobalOrderSearch class="ov-global-search" />
        </div>
        <div class="ov-bar-status">
          <span class="status-item">
            当前时刻 <b class="num">{{ displayTime || '--:--:--.---' }}</b>
          </span>
          <span class="status-item">
            changeIndex
            <Tooltip title="移动订单会改变该值">
              <b class="num">#{{ changeIndex }}</b>
            </Tooltip>
          </span>
          <span class="status-item">价差 <b class="num">{{ spreadText }}</b></span>
          <Popover placement="bottomRight" trigger="click">
            <template #content>
              <div class="locked-orders-popover">
                <div class="locked-orders-popover-header">
                  <span>已锁定订单（{{ lockedOrderIds.length }}）</span>
                  <Popconfirm
                    title="确定清除全部锁定订单吗？"
                    ok-text="确定"
                    cancel-text="取消"
                    @confirm="clearLockedOrders"
                  >
                    <Button type="link" size="small" danger>清除全部</Button>
                  </Popconfirm>
                </div>
                <div v-if="lockedOrderDetails.length" class="locked-order-list">
                  <div v-for="order in lockedOrderDetails" :key="order.id" class="locked-order-item">
                    <div class="locked-order-item-main">
                      <span class="locked-order-id">订单ID {{ order.id }}</span>
                      <Button type="link" size="small" danger @click="unlockOrder(order.id)">解锁</Button>
                    </div>
                    <div class="locked-order-item-meta">
                      <template v-if="order.visible">
                        {{ order.direction }}{{ order.levelLabel }} · 价格 {{ formatLockedPrice(order.price) }} ·
                        剩余量 {{ order.volume }}
                      </template>
                      <template v-else>当前快照不可见，仍可取消锁定</template>
                    </div>
                  </div>
                </div>
                <div v-else class="locked-orders-empty">暂无锁定订单</div>
              </div>
            </template>
            <span class="status-item locked-status" role="button" tabindex="0">
              已锁定 <b class="num">{{ lockedOrderIds.length }}</b> ▼
            </span>
          </Popover>
          <span class="status-item theme-toggle">
            暗色
            <Switch v-model:checked="darkMode" size="small" />
          </span>
          <span class="status-item ov-user-dropdown">
            <UserDropDown />
          </span>
        </div>
      </div>

      <!-- ① 顶栏 第2行：控制条 -->
      <div class="ov-topbar-row controls">
        <div class="ov-bar-group time-step-group">
          <span class="group-label">时间</span>
          <span class="step-label">小步</span>
          <TimeStepSelect v-model:value="smallStepMs" :presets="smallStepOptions" />
          <Button :disabled="isButtonDisabled" @click="moveTimes(-smallStepMs)">◀</Button>
          <Button :disabled="isButtonDisabled" @click="moveTimes(smallStepMs)">▶</Button>
          <span class="step-label">大步</span>
          <TimeStepSelect v-model:value="bigStepMs" :presets="bigStepOptions" />
          <Button :disabled="isButtonDisabled" @click="moveTimes(-bigStepMs)">◀◀</Button>
          <Button :disabled="isButtonDisabled" @click="moveTimes(bigStepMs)">▶▶</Button>
        </div>
        <div class="ov-bar-group">
          <span class="group-label">订单</span>
          <span class="step-label">步长</span>
          <Select
            v-model:value="orderStep"
            class="ov-select w-step"
            :options="orderStepOptions"
            :allow-clear="false"
          />
          <Button :disabled="isButtonDisabled" @click="moveticks(-orderStep)">◀</Button>
          <Button :disabled="isButtonDisabled" @click="moveticks(orderStep)">▶</Button>
        </div>
        <div class="ov-bar-group">
          <span class="group-label">跳转</span>
          <Input
            v-model:value="displayTime"
            placeholder="HH:mm:ss.SSS"
            class="ov-time-input"
            @change="handleTimeInputChange"
            @blur="handleTimeBlur"
          />
          <Button type="primary" :disabled="isButtonDisabled" @click="clickMoveTime">跳转</Button>
        </div>
        <div class="ov-bar-group">
          <span class="group-label">锁定</span>
          <Input v-model:value="lockByVolumeValue" placeholder="数量" class="ov-lock-input" />
          <Button type="primary" size="small" @click="lockOrdersByVolume">按量</Button>
          <Input v-model:value="lockByIdValue" placeholder="订单ID" class="ov-lock-input w-id" />
          <Button type="primary" size="small" @click="lockOrdersById(true)">按ID</Button>
          <Popconfirm
            title="确定清除全部锁定订单吗？"
            ok-text="确定"
            cancel-text="取消"
            @confirm="clearLockedOrders"
          >
            <Button size="small">清除全部</Button>
          </Popconfirm>
        </div>
      </div>
    </div>

    <!-- 主体：左数据区 + 右图表区 -->
    <div class="ov-body">
      <!-- ② 左侧数据区：买左卖右 -->
      <div class="ov-data-area">
        <div class="ov-queue-zones" :class="{ expanded: expandedTable }">
          <div v-show="!expandedTable || expandedTable.startsWith('bid')" class="ov-zone bid-zone">
            <VolumeDataTable
              :data="volumeData['bid1']?.data?.[0]?.[0] || {}"
              v-show="isTableVisible('bid1')"
              table-key="bid1"
              :derection="
                volumeData['bid1_price']
                  ? '  买一价：' + (volumeData['bid1_price'] / 10000).toFixed(volumeData.is_ETF ? 3 : 2)
                  : '  买一价：'
              "
              :is-fullscreen="expandedTable === 'bid1'"
              :search-value="searchValue"
              :locked-order-ids="lockedOrderIds"
              :cols="queueCols"
              v-on:toggle-lock="toggleOrderLock"
            @inspect-execution="openExecutionEstimate"
              @update-fullscreen="showHideTable"
            />
            <VolumeDataTable
              :data="volumeData['bid2']?.data?.[0]?.[0] || {}"
              v-show="isTableVisible('bid2')"
              table-key="bid2"
              :derection="
                volumeData['bid2_price']
                  ? '  买二价：' + (volumeData['bid2_price'] / 10000).toFixed(volumeData.is_ETF ? 3 : 2)
                  : '  买二价：'
              "
              :is-fullscreen="expandedTable === 'bid2'"
              :search-value="searchValue"
              :locked-order-ids="lockedOrderIds"
              :cols="queueCols"
              v-on:toggle-lock="toggleOrderLock"
            @inspect-execution="openExecutionEstimate"
              @update-fullscreen="showHideTable"
            />
            <VolumeDataTable
              :data="volumeData['bid3']?.data?.[0]?.[0] || {}"
              v-show="isTableVisible('bid3')"
              table-key="bid3"
              :derection="
                volumeData['bid3_price']
                  ? '  买三价：' + (volumeData['bid3_price'] / 10000).toFixed(volumeData.is_ETF ? 3 : 2)
                  : '  买三价：'
              "
              :is-fullscreen="expandedTable === 'bid3'"
              :search-value="searchValue"
              :locked-order-ids="lockedOrderIds"
              :cols="queueCols"
              v-on:toggle-lock="toggleOrderLock"
            @inspect-execution="openExecutionEstimate"
              @update-fullscreen="showHideTable"
            />
          </div>
          <div v-show="!expandedTable || expandedTable.startsWith('ask')" class="ov-zone ask-zone">
            <VolumeDataTable
              :data="volumeData['ask1']?.data?.[0]?.[0] || {}"
              v-show="isTableVisible('ask1')"
              table-key="ask1"
              :derection="
                volumeData['ask1_price']
                  ? '  卖一价：' + (volumeData['ask1_price'] / 10000).toFixed(volumeData.is_ETF ? 3 : 2)
                  : '  卖一价：'
              "
              :is-fullscreen="expandedTable === 'ask1'"
              :search-value="searchValue"
              :locked-order-ids="lockedOrderIds"
              :cols="queueCols"
              v-on:toggle-lock="toggleOrderLock"
            @inspect-execution="openExecutionEstimate"
              @update-fullscreen="showHideTable"
            />
            <VolumeDataTable
              :data="volumeData['ask2']?.data?.[0]?.[0] || {}"
              v-show="isTableVisible('ask2')"
              table-key="ask2"
              :derection="
                volumeData['ask2_price']
                  ? '  卖二价：' + (volumeData['ask2_price'] / 10000).toFixed(volumeData.is_ETF ? 3 : 2)
                  : '  卖二价：'
              "
              :is-fullscreen="expandedTable === 'ask2'"
              :search-value="searchValue"
              :locked-order-ids="lockedOrderIds"
              :cols="queueCols"
              v-on:toggle-lock="toggleOrderLock"
            @inspect-execution="openExecutionEstimate"
              @update-fullscreen="showHideTable"
            />
            <VolumeDataTable
              :data="volumeData['ask3']?.data?.[0]?.[0] || {}"
              v-show="isTableVisible('ask3')"
              table-key="ask3"
              :derection="
                volumeData['ask3_price']
                  ? '  卖三价：' + (volumeData['ask3_price'] / 10000).toFixed(volumeData.is_ETF ? 3 : 2)
                  : '  卖三价：'
              "
              :is-fullscreen="expandedTable === 'ask3'"
              :search-value="searchValue"
              :locked-order-ids="lockedOrderIds"
              :cols="queueCols"
              v-on:toggle-lock="toggleOrderLock"
            @inspect-execution="openExecutionEstimate"
              @update-fullscreen="showHideTable"
            />
          </div>
        </div>

      </div>

      <!-- ③ 右侧图表区：盘口统计表 + Q-t 窗口图 + 锁定订单图 -->
      <!-- 三张卡片按自然高度排列，内容超出时右栏整体滚动 -->
      <div class="ov-chart-area">
        <SummaryTables :level-data="levelData" :trade-data="tradeData" />
        <TradeFlowChart
          class="flow-card"
          :sym="selectSym"
          :date="selectDate"
          :current-time="selectTime"
          :ready="!isButtonDisabled"
          :dark="darkMode"
          :is-etf="isETF"
          @seek="handleChartSeek"
        />
        <LockedOrderChart
          v-if="lockedOrderIds.length"
          class="locked-order-card"
          :sym="selectSym"
          :date="selectDate"
          :locked-ids="lockedOrderIds"
          :volume-data="volumeData"
          :current-time="selectTime"
          :dark="darkMode"
          @seek="handleChartSeek"
        />
      </div>
    </div>

    <LockOrderSelector
      v-if="lockSelectorVisible"
      :visible="lockSelectorVisible"
      :candidates="lockCandidates"
      :sym="selectSym"
      :date="selectDate"
      :is-etf="isETF"
      @close="lockSelectorVisible = false"
      @confirm="handleLockConfirm"
    />
    <Modal
      :visible="executionEstimateVisible"
      :title="executionEstimateTitle"
      width="980px"
      :footer="null"
      centered
      destroy-on-close
      wrap-class-name="execution-estimate-modal"
      @cancel="closeExecutionEstimate"
      @update:visible="(visible) => { if (!visible) closeExecutionEstimate() }"
    >
      <Spin :spinning="executionEstimateLoading" tip="正在分析订单成交速度…">
        <div class="execution-estimate-body">
          <div v-if="executionEstimateError" class="estimate-error">
            {{ executionEstimateError }}
          </div>

          <template v-else-if="executionEstimateData">
            <div class="estimate-order-summary">
              <div>
                <span>方向</span>
                <b :class="executionEstimateData.order?.side === 1 ? 'bid-text' : 'ask-text'">
                  {{ executionEstimateData.order?.side === 1 ? '买' : '卖' }}
                </b>
              </div>
              <div><span>价格</span><b>{{ formatEstimatePrice(executionEstimateData.order?.price) }}</b></div>
              <div><span>原始数量</span><b>{{ formatEstimateVolume(executionEstimateData.order?.size) }} 手</b></div>
              <div><span>预测时点</span><b>{{ formatExecutionTime(executionEstimateData.as_of_time) }}</b></div>
            </div>

            <!-- 与窗口无关的不变量信息，统一置顶 -->
            <div v-if="executionEstimateData.current_queue" class="estimate-detail-grid">
              <div><span>当前档位</span><b>{{ executionEstimateData.current_queue.level || '--' }}</b></div>
              <div>
                <span>身前总量（含更高档位）</span>
                <b>{{ formatEstimateVolume(executionEstimateData.current_queue.ahead_total_volume ?? executionEstimateData.current_queue.ahead_volume) }} 手</b>
              </div>
              <div><span>其中更高档位量</span><b>{{ formatEstimateVolume(executionEstimateData.current_queue.higher_levels_volume) }} 手</b></div>
              <div><span>本单剩余量</span><b>{{ formatEstimateVolume(executionEstimateData.current_queue.remaining_volume) }} 手</b></div>
              <div><span>截至当前已成交</span><b>{{ formatEstimateVolume(executionEstimateData.prediction?.filled_volume_as_of) }} 手</b></div>
              <div><span>价格状态</span><b>{{ executionEstimateData.prediction?.price_active ? '当前最优价' : '当前非最优价' }}</b></div>
            </div>

            <section class="estimate-section prediction-section">
              <div class="estimate-section-title">
                <span>预测成交时间</span>
                <Tooltip placement="left" overlay-class-name="confidence-tip-overlay">
                  <template #title>
                    <div class="confidence-reasons">
                      <div>高：总分 ≥ 70 分</div>
                      <div>中：40 ≤ 总分 &lt; 70 分</div>
                      <div>低：总分 &lt; 40 分</div>
                      <template v-if="!executionEstimateData.prediction?.available">
                        <div
                          v-for="reason in executionEstimateData.prediction?.confidence_reasons || []"
                          :key="reason"
                          class="confidence-reason-note"
                        >
                          · {{ reason }}
                        </div>
                      </template>
                    </div>
                  </template>
                  <span
                    v-if="executionEstimateData.prediction?.confidence"
                    class="confidence-badge"
                    :class="'confidence-' + executionEstimateData.prediction.confidence"
                  >
                    可信度 {{ confidenceLabels[executionEstimateData.prediction.confidence] || '--' }}
                    <template v-if="executionEstimateData.prediction.confidence_score != null">
                      · {{ Math.round(executionEstimateData.prediction.confidence_score) }}分
                    </template>
                  </span>
                </Tooltip>
              </div>

              <div v-if="executionEstimateData.prediction?.available" class="estimate-time-grid">
                <div class="estimate-time-card">
                  <span>{{ executionEstimateData.prediction.has_filled_as_of ? '预计再次成交' : '预计首次成交' }}</span>
                  <strong>
                    {{
                      executionEstimateData.prediction.first_fill_beyond_close
                        ? '超过当日收盘'
                        : formatExecutionTime(executionEstimateData.prediction.first_fill_time)
                    }}
                  </strong>
                  <small>
                    还需
                    {{
                      formatEstimateWait(
                        executionEstimateData.prediction.first_fill_wait_ms,
                        executionEstimateData.prediction.first_fill_beyond_close,
                      )
                    }}
                  </small>
                </div>
                <div class="estimate-time-card">
                  <span>预计全部成交</span>
                  <strong>
                    {{
                      executionEstimateData.prediction.full_fill_beyond_close
                        ? '超过当日收盘'
                        : formatExecutionTime(executionEstimateData.prediction.full_fill_time)
                    }}
                  </strong>
                  <small>
                    还需
                    {{
                      formatEstimateWait(
                        executionEstimateData.prediction.full_fill_wait_ms,
                        executionEstimateData.prediction.full_fill_beyond_close,
                      )
                    }}
                  </small>
                </div>
              </div>
              <div v-else class="estimate-unavailable">
                {{ executionEstimateData.prediction?.reason || executionEstimateData.prediction?.warnings?.[0] || '当前样本不足，无法预测' }}
              </div>

              <!-- 逐窗口统计表：各窗口独立计算速率并给出预测，高亮行=头条预测所用窗口 -->
              <div v-if="executionEstimateData.prediction?.windows?.length" class="estimate-windows">
                <div class="estimate-windows-title">
                  分窗口统计（消耗/新增为{{ estimateSideLabel }}1口径<template v-if="hasSecondLevelNet">，{{ estimateSideLabel }}2净={{ estimateSideLabel }}2撤单−{{ estimateSideLabel }}2新增</template>；速率为 手/秒；可信度得分=样本量/稳定性/外推距离/显著性/观察时长加权，高亮行=得分最高的可计算窗口，即上方预测所用窗口）
                </div>
                <table class="estimate-window-table">
                  <thead>
                    <tr>
                      <th>时间窗口</th>
                      <th>消耗速度</th>
                      <th>新增挂单</th>
                      <th v-if="hasSecondLevelNet">{{ estimateSideLabel }}2净速度</th>
                      <th>净消耗速度</th>
                      <th>成交速度</th>
                      <th>可信度得分</th>
                      <th>预计首次成交</th>
                      <th>预计全部成交</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in executionEstimateData.prediction.windows"
                      :key="row.window_ms"
                      :class="{ 'row-selected': row.selected }"
                    >
                      <td>{{ formatEstimateWindow(row.actual_window_ms) }}</td>
                      <td>{{ formatWindowRate(row.depletion_rate) }}</td>
                      <td>{{ formatWindowRate(row.arrival_rate) }}</td>
                      <td v-if="hasSecondLevelNet">{{ formatWindowRate(row.second_net_rate) }}</td>
                      <td>{{ formatWindowRate(row.net_queue_rate) }}</td>
                      <td>{{ formatWindowRate(row.trade_rate) }}</td>
                      <td>
                        <Tooltip v-if="!row.available && row.reason" :title="row.reason">
                          <span class="cell-unavailable">--</span>
                        </Tooltip>
                        <Tooltip v-else placement="left" overlay-class-name="score-tip-overlay">
                          <template #title>
                            <table class="score-tip-table">
                              <tbody>
                                <tr v-for="item in scoreTipRows(row)" :key="item.name">
                                  <td class="score-tip-name">{{ item.name }}（{{ item.max }}分）</td>
                                  <td class="score-tip-value">{{ item.raw }} → {{ item.score }}分</td>
                                </tr>
                              </tbody>
                            </table>
                          </template>
                          <span
                            class="window-score"
                            :class="row.score >= 70 ? 'score-high' : row.score >= 40 ? 'score-medium' : 'score-low'"
                          >
                            {{ row.score }}
                          </span>
                        </Tooltip>
                      </td>
                      <td>
                        <Tooltip v-if="!row.available && row.reason" :title="row.reason">
                          <span class="cell-unavailable">不可估</span>
                        </Tooltip>
                        <template v-else>
                          {{ row.first_fill_beyond_close ? '超过收盘' : formatExecutionTime(row.first_fill_time) }}
                        </template>
                      </td>
                      <td>
                        <Tooltip v-if="!row.available && row.reason" :title="row.reason">
                          <span class="cell-unavailable">不可估</span>
                        </Tooltip>
                        <template v-else>
                          {{ row.full_fill_beyond_close ? '超过收盘' : formatExecutionTime(row.full_fill_time) }}
                        </template>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <ul v-if="executionEstimateData.prediction?.warnings?.length" class="estimate-warnings">
                <li v-for="warning in executionEstimateData.prediction.warnings" :key="warning">{{ warning }}</li>
              </ul>
            </section>

            <section class="estimate-section actual-section">
              <div class="estimate-section-title">真实成交结果（完整交易日）</div>
              <div class="estimate-detail-grid">
                <div><span>最终结果</span><b>{{ executionEstimateData.actual?.outcome || '--' }}</b></div>
                <div><span>真实首次成交</span><b>{{ formatExecutionTime(executionEstimateData.actual?.first_fill_time) }}</b></div>
                <div><span>真实全部成交</span><b>{{ formatExecutionTime(executionEstimateData.actual?.full_fill_time) }}</b></div>
                <div><span>最后一次成交</span><b>{{ formatExecutionTime(executionEstimateData.actual?.last_fill_time) }}</b></div>
                <div><span>撤单时间</span><b>{{ formatExecutionTime(executionEstimateData.actual?.cancel_time) }}</b></div>
                <div><span>成交次数</span><b>{{ executionEstimateData.actual?.trade_count || 0 }} 次</b></div>
                <div><span>实际成交量</span><b>{{ formatEstimateVolume(executionEstimateData.actual?.filled_volume) }} 手</b></div>
                <div><span>实际撤单量</span><b>{{ formatEstimateVolume(executionEstimateData.actual?.cancelled_volume) }} 手</b></div>
              </div>
            </section>

            <div class="estimate-footnote">
              预测严格只使用 {{ formatExecutionTime(executionEstimateData.as_of_time) }} 及之前的数据；
              “真实成交结果”单独读取完整交易日，仅用于事后对照，不参与预测。
            </div>
          </template>

          <div v-else-if="!executionEstimateLoading" class="estimate-empty">暂无分析数据</div>
        </div>
      </Spin>
    </Modal>

  </div>
</template>

<script lang="js" setup>
    import { ref, computed, onMounted, onUnmounted, watch, h, nextTick } from 'vue';
    import { useRoute, useRouter } from 'vue-router';
    import { Table, Empty, Spin, Radio, Button, Select, Input, Tooltip, Progress, Switch, Popover, Popconfirm, Modal } from 'ant-design-vue';
  import { QuestionCircleOutlined } from '@ant-design/icons-vue';
    import { getSymList, getDateList, getVolumeData, getDatetimeList, getVolumeDataByTime, getSnapshotById, getSnapshotByIndex, getSnapshotByTime, getPastTimeTradeInfo, getNextChange, initTradeBook, getProgress, checkServerStatus, getOrderExecutionEstimate } from '/@/api/orderbook/orderbook';

    // 调试日志：仅开发环境且 localStorage.ov_debug === '1' 时输出，
    // 生产构建静默（替代原先 120+ 处无条件 console.log）
    const debugLog = (...args) => {
      if (import.meta.env.DEV && localStorage.getItem('ov_debug') === '1') {
        console.log(...args);
      }
    };
    import VolumeDataTable from './components/VolumeTable.vue'
    import LockOrderSelector from './components/LockOrderSelector.vue'
    import TradeFlowChart from './components/TradeFlowChart.vue'
    import LockedOrderChart from './components/LockedOrderChart.vue'
    import TimeStepSelect from './components/TimeStepSelect.vue'
    import SummaryTables from './components/SummaryTables.vue'
    // 顶部布局 header 移除后迁入：订单查询跳转（开始按钮右侧）、登录状态（暗色开关右侧）
    import GlobalOrderSearch from '/@/layouts/default/header/components/GlobalOrderSearch.vue'
    import UserDropDown from '/@/layouts/default/header/components/user-dropdown/index.vue'
    import { useSnapshotNavigation } from './composables/useSnapshotNavigation';
    import { useMessage } from '/@/hooks/web/useMessage';
    import { createProgressListener } from '/@/utils/websocket';
    import { useOrderLockStore } from '/@/store/orderLock';
    import {
      getSecurityType,
      securityTypeOptions,
      toSymbolOption,
    } from '/@/utils/securityType';
    import { 
      validateOrderLockData, 
      withOrderLockErrorHandling, 
      showOrderLockFeedback, 
      handleOrderLockError,
      cleanLockParamsFromUrl,
      formatTimestamp 
    } from '/@/logics/orderLockUtils';

    const loading = ref(true);
    const messageText = ref('');
    const volumeData = ref({});
    const isETF = ref(false);
    const { createMessage } = useMessage();

    // 快照导航状态（第 5 步抽取）：「当前时刻」唯一状态源，图表区只读跟随
    const {
      selectTime,
      changeIndex,
      orderIndex,
      snapshotId,
      displayTime,
      parseTimeToMs,
      formatMsToTimeStr,
      extractTimeFromTimestamp,
      isValidTimeFormat,
      checkTimeBoundary,
    } = useSnapshotNavigation();

    const securityType = ref(localStorage.getItem('volumeQueue_securityType') || 'stock');
    const selectSym = ref('');
    const symsData = ref([]);
    const hasSymbol = ref(false);

    const filteredSymsData = computed(() => {
      return symsData.value.filter((option) => option.securityType === securityType.value);
    });

    const selectDate = ref('');
    const datesData = ref([]);
    const hasDate = ref(false);

    // 初始化Pinia状态管理
    const orderLockStore = useOrderLockStore();
    
    // 获取路由实例
    const route = useRoute();
    const router = useRouter();
    
    debugLog('🔧 [VolumeQueue] ========== 初始化 Pinia Store 监听 ==========');
    debugLog('   - orderLockStore 已创建');
    debugLog('   - 初始 hasLockData:', orderLockStore.hasLockData);
    debugLog('   - 初始 lockData:', orderLockStore.lockData);
    
    // 监听 Pinia store 中的锁定数据变化
    watch(() => orderLockStore.hasLockData, async (hasData, oldValue) => {
      debugLog('🔔 [VolumeQueue] ========== Pinia Store 变化检测 ==========');
      debugLog('   - 旧值 (oldValue):', oldValue);
      debugLog('   - 新值 (hasData):', hasData);
      debugLog('   - lockData:', JSON.stringify(orderLockStore.lockData, null, 2));
      
      if (hasData) {
        debugLog('✅ [VolumeQueue] 检测到 Pinia 中有新的锁定数据，开始处理');
        
        // 延迟一下确保数据完全写入
        await nextTick();
        
        // 使用统一的订单锁定处理器
        await withOrderLockErrorHandling(async () => {
          // 验证锁定数据
          const validationResult = validateOrderLockData(orderLockStore.lockData);
          if (!validationResult.success) {
            showOrderLockFeedback(validationResult);
            return;
          }
          
          debugLog('🔄 [VolumeQueue] 使用 Pinia 数据处理订单锁定');
          
          // 调用统一的订单锁定处理器
          await unifiedOrderLockHandler(validationResult.data);
          
          // 清理 Pinia 状态中的锁定数据
          orderLockStore.clearLockData();
          
          debugLog('✅ [VolumeQueue] Pinia 订单锁定处理完成');
        }, '订单锁定流程');
      }
    }, { immediate: true }); // 立即执行一次，检查是否已有数据
    
    debugLog('✓ [VolumeQueue] Pinia Store 监听器已设置');
    
    // 处理来自 GlobalOrderSearch 的锁定请求（使用 sessionStorage，刷新后不保留）
    // 处理来自 GlobalOrderSearch 的自动锁定请求（顺序 await，替代 setTimeout 魔法链）
    const handleGlobalSearchLockRequest = async () => {
      // GlobalOrderSearch 同时写入 Pinia 和 sessionStorage。Pinia 流程优先，
      // 避免两个自动锁定流程并发修改 volumeData，导致快照时间互相覆盖。
      if (orderLockStore.hasLockData) {
        [
          volumeQueue_isFromGlobalSearch,
          volumeQueue_autoLock,
          volumeQueue_lockTimestamp,
          volumeQueue_lockOrderId,
          volumeQueue_lockSym,
          volumeQueue_lockDate,
        ].forEach((key) => sessionStorage.removeItem(key));
        return;
      }

      // 只从 sessionStorage 读取来自 GlobalOrderSearch 的锁定请求
      const isFromGlobalSearch = sessionStorage.getItem('volumeQueue_isFromGlobalSearch');
      const autoLock = sessionStorage.getItem('volumeQueue_autoLock');
      const lockTime = sessionStorage.getItem('volumeQueue_lockTimestamp'); // 用户输入的时间
      const lockOrderId = sessionStorage.getItem('volumeQueue_lockOrderId');
      const lockSym = sessionStorage.getItem('volumeQueue_lockSym');
      const lockDate = sessionStorage.getItem('volumeQueue_lockDate');

      // 先清除 sessionStorage 标记，避免后续刷新重复触发
      sessionStorage.removeItem('volumeQueue_isFromGlobalSearch');
      sessionStorage.removeItem('volumeQueue_autoLock');
      sessionStorage.removeItem('volumeQueue_lockTimestamp');
      sessionStorage.removeItem('volumeQueue_lockOrderId');
      sessionStorage.removeItem('volumeQueue_lockSym');
      sessionStorage.removeItem('volumeQueue_lockDate');

      if (!(isFromGlobalSearch === 'true' && autoLock === 'true' && lockTime && lockOrderId)) {
        return;
      }

      try {
        // 步骤1：设置股票代码和日期（如果提供）
        if (lockSym && lockDate) {
          securityType.value = getSecurityType(lockSym);
          selectSym.value = lockSym;
          selectDate.value = lockDate;
          hasSymbol.value = true;
          hasDate.value = true;
          localStorage.setItem('volumeQueue_selectSym', lockSym);
          localStorage.setItem('volumeQueue_securityType', securityType.value);
          localStorage.setItem('volumeQueue_selectDate', lockDate);
        } else {
          createMessage.warning('缺少股票代码或日期，无法自动锁定');
          return;
        }

        // 步骤2：填充跳转时间与订单ID输入框
        selectTime.value = lockTime;
        lockByIdValue.value = lockOrderId;

        // 步骤3：加载日期列表（如果尚未加载）
        if (!datesData.value.length) {
          loading.value = true;
          try {
            const res = await getDateList({ sym: lockSym });
            if (res.code == 0) {
              datesData.value = res.data.map(item => ({ label: item, value: item }));
            }
          } finally {
            loading.value = false;
          }
        }

        // 步骤4：初始化 TradeBook 并加载数据
        if (!(hasSymbol.value && hasDate.value && !loading.value)) {
          createMessage.warning('页面状态不满足加载条件，请手动点击「开始」');
          return;
        }
        await getOrderbookData();
        // getOrderbookData 会重置 selectTime 为 09:30:00.000，重新设置为用户输入的时间
        selectTime.value = lockTime;

        // 等待首个快照数据就绪（条件轮询，替代固定 2000ms 魔法延时）
        const waitStart = Date.now();
        while (!(volumeData.value && Object.keys(volumeData.value).length > 0)) {
          if (Date.now() - waitStart > 30000) {
            createMessage.warning('数据加载超时，请稍后手动操作');
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, 100));
        }

        // 步骤5：跳转到创建时间对应的快照
        await clickMoveTime(lockTime);

        // 创建事件时间可能早于订单进入盘口的第一条快照，沿变化点向前补齐。
        if (!isOrderVisibleInCurrentSnapshot(lockOrderId)) {
          await findFirstSnapshotContainingOrder(lockOrderId, lockTime);
        }

        // 步骤6：锁定订单
        const lockSuccess = lockOrdersById(true);
        if (lockSuccess) {
          createMessage.success('订单已自动锁定并定位');
        } else {
          createMessage.warning('订单锁定失败，请手动点击「按ID」按钮');
        }
      } catch (error) {
        console.error('处理订单锁定请求失败:', error);
        createMessage.error('处理订单锁定请求失败');
      }
    };

    // 组件初始化
    // localStorage 语义约定：volumeQueue_* 键仅用于「同一次会话内」跨组件共享当前选择
    // （如 GlobalOrderSearch 预填），刷新页面一律清空回到初始状态，不做刷新恢复。
    onMounted(async () => {
      // 清除 volumeQueue_* localStorage（每次刷新都清空，干净起点）
      const keysToRemove = [
        'volumeQueue_selectSym',
        'volumeQueue_selectDate',
        'volumeQueue_securityType',
        'volumeQueue_timestamp',
        'volumeQueue_lockOrderId',
        'volumeQueue_isFromGlobalSearch',
        'volumeQueue_autoLock',
        'volumeQueue_orderIndex',
        'volumeQueue_userInputTime'
      ];
      keysToRemove.forEach(key => localStorage.removeItem(key));

      // 当前页面内的查询请求优先走 Pinia。只有在没有 Pinia 请求时，
      // 才清理旧状态并使用 sessionStorage 兼容流程，避免两个流程并发。
      const hasPiniaLockRequest = orderLockStore.hasLockData;
      if (hasPiniaLockRequest) {
        // 仅借用兼容入口清理同一请求留下的 sessionStorage 标记；该函数会立即返回。
        await handleGlobalSearchLockRequest();
      } else {
        orderLockStore.clearLockData();
        // 检查是否有来自 GlobalOrderSearch 的 sessionStorage 兼容请求
        await handleGlobalSearchLockRequest();
      }

      // 解决aria-hidden警告
      nextTick(() => {
        const activeElement = document.activeElement;
        if (activeElement) {
          const closestAriaHidden = activeElement.closest('[aria-hidden="true"]');
          if (closestAriaHidden) {
            document.body.focus();
          }
        }
      });
    });

    // 处理时间输入变化
    

    const handleTimeInputChange = (e) => {
      const inputValue = e.target.value;
      // 放宽：变更时不做边界检测，直接更新
      selectTime.value = inputValue;
    };

    // 失焦时再做边界检测与持久化
    const handleTimeBlur = (e) => {
      const raw = e?.target?.value ?? selectTime.value;
      
      // 如果输入为空，不做处理
      if (!raw || raw.trim() === '') {
        localStorage.setItem('volumeQueue_timestamp', '');
        return;
      }
      
      const checkResult = checkTimeBoundary(raw);
      
      if (!checkResult.valid) {
        // 时间无效或超出范围，自动修正并提示用户
        const oldValue = selectTime.value;
        selectTime.value = checkResult.corrected;
        
        // 根据不同的原因给出不同的提示
        let message = '';
        switch (checkResult.reason) {
          case 'format':
            message = `时间格式无效，已自动调整为 ${checkResult.corrected}`;
            break;
          case 'parse':
            message = `无法解析时间，已自动调整为 ${checkResult.corrected}`;
            break;
          case 'early':
            message = `时间早于开盘时间（09:30），已自动调整为 ${checkResult.corrected}`;
            break;
          case 'late':
            message = `时间晚于收盘时间（15:00），已自动调整为 ${checkResult.corrected}`;
            break;
          default:
            message = `时间已自动调整为 ${checkResult.corrected}`;
        }
        
        createMessage.warning(message);
        debugLog(`📝 时间自动修正: "${oldValue}" → "${checkResult.corrected}" (原因: ${checkResult.reason})`);
      }
      
      localStorage.setItem('volumeQueue_timestamp', selectTime.value || '');
    };
    
    const expandedTable = ref(null);
    const timesData = ref([]);

    // 搜索相关状态
    const searchValue = ref('');
    const lockByVolumeValue = ref('');
    const lockByIdValue = ref('');
    const lockedOrderIds = ref([]);

    // 右键订单成交时间分析弹窗
    const executionEstimateVisible = ref(false);
    const executionEstimateLoading = ref(false);
    const executionEstimateError = ref('');
    const executionEstimateData = ref(null);
    const executionEstimateOrder = ref(null);
    let executionEstimateRequestSeq = 0;
    
    // 订单锁定进度状态
    const lockProgress = ref({
      show: false,
      message: '准备锁定订单...',
      detail: '', // 详细信息，显示在进度条下方
      progress: 0,
      status: 'processing', // processing, completed, failed
      currentStep: 0,
      totalSteps: 7
    });
    
    // 订单锁定进度条通知实例
    let lockProgressMessageInstance = null;
    const LOCK_PROGRESS_MESSAGE_KEY = 'order-lock-progress-notification';

    // 初始化进度状态
    const initProgress = ref({
      show: false,
      message: '等待开始初始化...',
      taskId: null,
      progress: 0,
      status: 'initializing'
    });
    
    // 创建订单锁定进度条通知内容（带详细信息）
    const createLockProgressContent = () => {
      const children = [
        // 第一行：进度条和主要信息
        h('div', {
          style: 'display: flex; align-items: center; min-width: 320px;'
        }, [
          h(Progress, {
            percent: lockProgress.value.progress,
            strokeColor: getLockProgressColor(lockProgress.value.status),
            showInfo: false,
            strokeWidth: 6,
            style: 'width: 220px; margin-right: 12px;'
          }),
          h('span', {
            style: 'font-size: 12px; color: #666; white-space: nowrap; min-width: 80px;'
          }, `步骤 ${lockProgress.value.currentStep}/${lockProgress.value.totalSteps} | ${lockProgress.value.message}`)
        ])
      ];
      
      // 第二行：详细信息（如果有）
      if (lockProgress.value.detail) {
        children.push(
          h('div', {
            style: 'margin-top: 6px; font-size: 11px; color: #999; padding-left: 4px;'
          }, lockProgress.value.detail)
        );
      }
      
      return h('div', {
        style: 'display: flex; flex-direction: column;',
        key: `lock-progress-${lockProgress.value.progress}-${Date.now()}`
      }, children);
    };
    
    // 创建持久的订单锁定进度条通知
    const createLockProgressNotification = () => {
      if (lockProgressMessageInstance) {
        return;
      }
      
      createMessage.open({
        content: createLockProgressContent(),
        duration: 0,
        type: 'loading',
        key: LOCK_PROGRESS_MESSAGE_KEY,
        style: {
          marginTop: '10px'
        }
      });
      
      lockProgressMessageInstance = true;
    };
    
    // 更新订单锁定进度条通知内容
    const updateLockProgressNotification = () => {
      if (!lockProgressMessageInstance) {
        createLockProgressNotification();
        return;
      }
      
      const messageType = lockProgress.value.status === 'completed' ? 'success' :
                         lockProgress.value.status === 'failed' ? 'error' : 'loading';
      
      createMessage.open({
        content: createLockProgressContent(),
        duration: 0,
        type: messageType,
        key: LOCK_PROGRESS_MESSAGE_KEY,
        style: {
          marginTop: '10px'
        }
      });
    };
    
    // 关闭订单锁定进度条通知
    const closeLockProgressNotification = () => {
      if (lockProgressMessageInstance) {
        createMessage.destroy(LOCK_PROGRESS_MESSAGE_KEY);
        lockProgressMessageInstance = null;
      }
    };
    
    // 获取订单锁定进度条颜色
    const getLockProgressColor = (status) => {
      switch (status) {
        case 'processing':
          return '#1890ff'; // 蓝色
        case 'completed':
          return '#52c41a'; // 绿色
        case 'failed':
          return '#ff4d4f'; // 红色
        default:
          return '#1890ff';
      }
    };

    // 进度条通知实例状态
    let progressMessageInstance = null;
    const PROGRESS_MESSAGE_KEY = 'orderbook-progress-notification';

    // 创建进度条通知内容
    const createProgressContent = () => {
      return h('div', {
        style: 'display: flex; align-items: center; min-width: 320px;',
        key: `progress-${initProgress.value.progress}-${Date.now()}` // 添加key确保更新
      }, [
        h(Progress, {
          percent: initProgress.value.progress,
          strokeColor: getProgressColor(initProgress.value.status),
          showInfo: false,
          strokeWidth: 6,
          style: 'width: 220px; margin-right: 12px;'
        }),
        h('span', {
          style: 'font-size: 12px; color: #666; white-space: nowrap; min-width: 80px;'
        }, `${initProgress.value.progress}% | ${initProgress.value.message}`)
      ]);
    };

    // 创建持久的进度条通知
    const createProgressNotification = () => {
      // 如果已经有通知实例，直接返回
      if (progressMessageInstance) {
        return;
      }

      // 创建持久的进度通知，使用固定key确保唯一性
      createMessage.open({
        content: createProgressContent(),
        duration: 0, // 不自动关闭
        type: 'loading',
        key: PROGRESS_MESSAGE_KEY, // 使用固定key确保不被其他通知覆盖
        style: {
          marginTop: '10px'
        }
      });

      progressMessageInstance = true; // 标记已创建
    };

    // 更新进度条通知内容
    const updateProgressNotification = () => {
      if (!progressMessageInstance) {
        createProgressNotification();
        return;
      }

      // 使用相同的key更新通知内容，而不是创建新通知
      const messageType = initProgress.value.status === 'completed' ? 'success' :
                         initProgress.value.status === 'failed' ? 'error' : 'loading';

      createMessage.open({
        content: createProgressContent(),
        duration: 0,
        type: messageType,
        key: PROGRESS_MESSAGE_KEY, // 使用相同的key确保更新而不是创建新通知
        style: {
          marginTop: '10px'
        }
      });
    };

    // 关闭进度条通知
    const closeProgressNotification = () => {
      if (progressMessageInstance) {
        createMessage.destroy(PROGRESS_MESSAGE_KEY); // 使用key销毁特定通知
        progressMessageInstance = null;
      }
    };

    // WebSocket 连接实例
    let progressWebSocket = null;

    // 初始化默认的空数据，确保表格始终显示
    const levelData = ref([
      { level: '买一', volume: '', number: '' },
      { level: '买二', volume: '', number: '' },
      { level: '买三', volume: '', number: '' },
      { level: '卖一', volume: '', number: '' },
      { level: '卖二', volume: '', number: '' },
      { level: '卖三', volume: '', number: '' }
    ]);

    const tradeData = ref([
      { level: '买一新增撤单', last_1min: '', last_3s: '', last_500ms: '', last_50ms: '', last_10ms: '' },
      { level: '买一新增挂单', last_1min: '', last_3s: '', last_500ms: '', last_50ms: '', last_10ms: '' },
      { level: '买一新增成交', last_1min: '', last_3s: '', last_500ms: '', last_50ms: '', last_10ms: '' },
      { level: '卖一新增撤单', last_1min: '', last_3s: '', last_500ms: '', last_50ms: '', last_10ms: '' },
      { level: '卖一新增挂单', last_1min: '', last_3s: '', last_500ms: '', last_50ms: '', last_10ms: '' },
      { level: '卖一新增成交', last_1min: '', last_3s: '', last_500ms: '', last_50ms: '', last_10ms: '' }
    ]);

    // 市场数据相关变量
    const marketData = ref({
      bid_create_count: 0,
      bid_cancel_count: 0,
      bid_traded_count: 0,
      ask_create_count: 0,
      ask_cancel_count: 0,
      ask_traded_count: 0
    });

    const level_columns = [
      {
        title: '档位',
        dataIndex: 'level',
        key: 'level',
        width: 90,
        className: 'header-transaction-amount',
        sorter: (a, b) => a.level - b.level,
        align: 'center',
      },
      {
        title: '总数量',
        dataIndex: 'volume',
        key: 'volume',
        width: 90,
        className: 'header-transaction-amount',
        sorter: (a, b) => a.volume - b.volume,
        align: 'center',
      },
      {
        title: '总订单数',
        dataIndex: 'number',
        key: 'number',
        width: 90,
        className: 'header-transaction-amount',
        sorter: (a, b) => a.number - b.number,
        align: 'center',
      },
    ]

    const trader_columns = [
      {
        title: '档位',
        dataIndex: 'level',
        key: 'level',
        width: 90,
        className: 'header-transaction-amount',
        sorter: (a, b) => a.level - b.level,
        align: 'center',
      },
      {
        title: '过去一分钟',
        dataIndex: 'last_1min',
        key: 'last_1min',
        width: 90,
        className: 'header-transaction-amount',
        sorter: (a, b) => a.last_1min - b.last_1min,
        align: 'center',
      },
      {
        title: '过去3s',
        dataIndex: 'last_3s',
        key: 'last_3s',
        width: 90,
        className: 'header-transaction-amount',
        sorter: (a, b) => a.last_3s - b.last_3s,
        align: 'center',
      },
      {
        title: '过去500ms',
        dataIndex: 'last_500ms',
        key: 'last_500ms',
        width: 90,
        className: 'header-transaction-amount',
        sorter: (a, b) => a.last_500ms - b.last_500ms,
        align: 'center',
      },
      {
        title: '过去50ms',
        dataIndex: 'last_50ms',
        key: 'last_50ms',
        width: 90,
        className: 'header-transaction-amount',
        sorter: (a, b) => a.last_50ms - b.last_50ms,
        align: 'center',
      },
      {
        title: '过去10ms',
        dataIndex: 'last_10ms',
        key: 'last_10ms',
        width: 90,
        className: 'header-transaction-amount',
        sorter: (a, b) => a.last_10ms - b.last_10ms,
        align: 'center',
      },
    ]

    const isButtonDisabled = computed(() => {
      return Object.keys(volumeData.value).length === 0;
    });

    // ==================== 第 4 步：布局与视觉重构新增状态 ====================

    // A1 步进颗粒度选择器（预设为毫秒数，支持输入「数字+ms/s/min」自定义）
    // 小步上限 500ms、大步入门 1s，两档预设不重叠；
    // 小步最小 10ms——订单最小时间间隔就是 10ms，更小的步长无意义
    const smallStepMs = ref(30);
    const smallStepOptions = [10, 30, 50, 100, 200, 500];
    const bigStepMs = ref(1000);
    const bigStepOptions = [1000, 3000, 5000, 10000, 30000, 60000, 300000];
    const orderStep = ref(1);
    const orderStepOptions = [
      { value: 1, label: '±1 订单' },
      { value: 10, label: '±10 订单' },
      { value: 100, label: '±100 订单' },
      { value: 500, label: '±500 订单' },
    ];
    // 队列格子列数（买左卖右布局下每档 6 列，全屏时 VolumeTable 固定 24 列）
    const queueCols = 6;

    // 买一卖一价差展示
    const spreadText = computed(() => {
      const a = volumeData.value?.ask1_price;
      const b = volumeData.value?.bid1_price;
      if (!a || !b) return '--';
      return ((a - b) / 10000).toFixed(volumeData.value.is_ETF ? 3 : 2);
    });

    // D1 暗色主题（可选，浅色默认；跨刷新保留偏好）
    const darkMode = ref(localStorage.getItem('ov_theme') === 'dark');
    watch(darkMode, (v) => {
      localStorage.setItem('ov_theme', v ? 'dark' : 'light');
    });

    // A4 快捷键：←/→ 小步，Shift+←/→ 大步，Ctrl(Alt)+←/→ 订单步进
    const handleKeydown = (e) => {
      const tag = (e.target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target?.isContentEditable) return;
      if (isButtonDisabled.value) return;
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const dir = e.key === 'ArrowLeft' ? -1 : 1;
      if (e.shiftKey) {
        moveTimes(dir * bigStepMs.value);
      } else if (e.ctrlKey || e.altKey) {
        moveticks(dir * orderStep.value);
      } else {
        moveTimes(dir * smallStepMs.value);
      }
    };
    onMounted(() => window.addEventListener('keydown', handleKeydown));
    onUnmounted(() => window.removeEventListener('keydown', handleKeydown));

    const handleSecurityTypeChange = (type) => {
      securityType.value = type;
      localStorage.setItem('volumeQueue_securityType', type);

      // 类型切换后清空旧代码及其关联日期，避免股票代码和基金代码混用。
      selectSym.value = '';
      hasSymbol.value = false;
      selectDate.value = '';
      datesData.value = [];
      hasDate.value = false;
      volumeData.value = {};
      isETF.value = type === 'fund';
      localStorage.removeItem('volumeQueue_selectSym');
      localStorage.removeItem('volumeQueue_selectDate');
      localStorage.removeItem('volumeQueue_timestamp');
    };

    const symFilterOption = (input, option) => {
      return option.value.toLowerCase().indexOf(input.toLowerCase()) >= 0;
    };

    const selectSymItem = (item) => {
      selectSym.value = item
      // 输入已被点选消费，清掉搜索缓存，避免 blur 时残留串覆盖本次选择
      symSearchValue.value = ''
      symLastValidInput.value = ''
      securityType.value = getSecurityType(item)
      localStorage.setItem('volumeQueue_securityType', securityType.value)
      localStorage.setItem('volumeQueue_selectSym', item)
      hasSymbol.value = true
      loading.value = true
      let params = {
        sym: item,
      }
      getDateList(params).then((res) => {
        loading.value = false
        if (res.code == 0) {
          datesData.value = []
          res.data.forEach((item) => {
            datesData.value.push({ label: item, value: item })
          })
          if (res.message) {
            createMessage.success(res.message)
          }
        } else {
            messageText.value = res.msg
            createMessage.error(res.message || res.msg || '获取日期列表失败')
          }
      }).catch((error) => {
        loading.value = false
        createMessage.error('网络请求失败，请检查网络连接')
      })
    }

    const dateFilterOption = (input, option) => {
      return option.value.toLowerCase().indexOf(input.toLowerCase()) >= 0;
    };

    const selectDateItem = async (item) => {
      selectDate.value = item
      localStorage.setItem('volumeQueue_selectDate', item)
      hasDate.value = true
      // 输入已被点选消费，清掉搜索缓存，避免 blur 时残留串覆盖本次选择
      dateSearchValue.value = ''
      dateLastValidInput.value = ''
    }

    // 存储用户输入的搜索内容
    const symSearchValue = ref('');
    const dateSearchValue = ref('');

    // 存储用户的最后有效输入
    const symLastValidInput = ref('');
    const dateLastValidInput = ref('');

    // 处理股票代码焦点事件
    const handleSymFocus = () => {
      // 焦点事件处理
    }

    // 处理股票代码输入事件
    const handleSymInput = (event) => {
      // 输入事件处理
    }

    // 处理股票代码搜索事件
    const handleSymSearch = (value) => {
      symSearchValue.value = value;

      // 如果有有效输入（非空），保存为最后有效输入
      if (value && value.trim() !== '') {
        symLastValidInput.value = value.trim();
      }
    }

    // 处理日期搜索事件
    const handleDateSearch = (value) => {
      dateSearchValue.value = value;

      // 如果有有效输入（非空），保存为最后有效输入
      if (value && value.trim() !== '') {
        dateLastValidInput.value = value.trim();
      }
    }

    // 处理股票代码失焦事件
    const handleSymBlur = () => {
      // 使用setTimeout确保在blur事件后执行，避免与Select组件内部逻辑冲突
      setTimeout(() => {
        // 当前选中值已是合法选项时，尊重用户的精确选择，不做任何覆盖
        // （与日期侧同理：防止残留搜索串的模糊匹配覆盖用户点选的结果）
        const currentValid = filteredSymsData.value.find(option =>
          option.value.toLowerCase() === String(selectSym.value || '').toLowerCase()
        );
        if (currentValid) {
          symSearchValue.value = '';
          symLastValidInput.value = '';
          return;
        }

        // 仅当当前值为空/无效时，才用未提交的输入回填；只认完全匹配
        const searchInput = (symLastValidInput.value || symSearchValue.value || '').trim();
        if (searchInput) {
          const matchingOption = filteredSymsData.value.find(option =>
            option.value.toLowerCase() === searchInput.toLowerCase()
          );

          if (matchingOption) {
            selectSymItem(matchingOption.value);
          } else {
            // 如果没有找到匹配项，清空输入并提示用户
            selectSym.value = '';
            createMessage.warning(`未找到匹配的${securityType.value === 'fund' ? '基金' : '股票'}代码: ${searchInput}`);
          }
        }

        // 清空搜索值和最后有效输入
        symSearchValue.value = '';
        symLastValidInput.value = '';
      }, 100);
    }

    // 处理日期失焦事件
    const handleDateBlur = () => {
      // 使用setTimeout确保在blur事件后执行，避免与Select组件内部逻辑冲突
      setTimeout(() => {
        // 当前选中值已是合法选项时，尊重用户的精确选择，不做任何覆盖。
        // （修复：搜索过滤后点选相邻日期，曾被残留搜索串的模糊匹配覆盖回旧日期）
        const currentValid = datesData.value.find(option =>
          option.value.toLowerCase() === String(selectDate.value || '').toLowerCase()
        );
        if (currentValid) {
          dateSearchValue.value = '';
          dateLastValidInput.value = '';
          return;
        }

        // 仅当当前值为空/无效时，才用未提交的输入回填；只认完全匹配——
        // 部分串匹配在降序列表里命中哪天不可预测，不再自动选中
        const searchInput = (dateLastValidInput.value || dateSearchValue.value || '').trim();
        if (searchInput) {
          const matchingOption = datesData.value.find(option =>
            option.value.toLowerCase() === searchInput.toLowerCase()
          );

          if (matchingOption) {
            selectDateItem(matchingOption.value);
          } else {
            // 如果没有找到匹配项，清空输入并提示用户
            selectDate.value = '';
            createMessage.warning(`未找到匹配的日期: ${searchInput}`);
          }
        }

        // 清空搜索值
        dateSearchValue.value = '';
        dateLastValidInput.value = '';
      }, 100);
    }

    const timeFilterOption = (input, option) => {
      // return option.value.toLowerCase().indexOf(input.toLowerCase()) >= 0;
      return option.value.startsWith(input);
    }

    const clickMoveTime = (targetTime = null) => {      // 从localStorage读取存储的单号和日期
      const savedSym = localStorage.getItem('volumeQueue_selectSym');
      const savedDate = localStorage.getItem('volumeQueue_selectDate');
      
      // 优先使用localStorage中的值
      const finalSym = savedSym || selectSym.value;
      const finalDate = savedDate || selectDate.value;
      
      // 如果有存储的单号和日期，更新当前选择
      if (finalSym && finalDate) {
        selectSym.value = finalSym;
        hasSymbol.value = true;
        selectDate.value = finalDate;
        hasDate.value = true;
      } else if (finalSym || finalDate) {
        // 如果只有其中一个值，提示用户需要完整选择
        createMessage.warning('请完整选择单号和日期');
        return;
      } else {
        // 如果没有存储的值，提示用户需要先选择
        createMessage.warning('请先选择单号和日期');
        return;
      }

      // 修复：检查 targetTime 是否为事件对象
      // 如果是事件对象（有 isTrusted 属性），则忽略它
      let inputTime;
      if (targetTime && typeof targetTime === 'object' && 'isTrusted' in targetTime) {
        // 这是一个事件对象，使用 selectTime.value
        inputTime = selectTime.value;
        debugLog('clickMoveTime: 检测到事件对象，使用 selectTime.value:', inputTime);
      } else if (targetTime && typeof targetTime === 'string') {
        // 这是一个有效的时间字符串参数
        inputTime = targetTime;
        debugLog('clickMoveTime: 使用传入的时间参数:', inputTime);
      } else {
        // 没有参数或参数无效，使用 selectTime.value
        inputTime = selectTime.value;
        debugLog('clickMoveTime: 使用 selectTime.value:', inputTime);
      }
      
      // 验证时间是否为空
      if (!inputTime || inputTime.trim() === '') {
        createMessage.warning('请输入跳转时间');
        return;
      }
      
      // 不做边界检查，直接使用用户输入的时间
      // 执行完整的数据处理逻辑，传递第四个参数表示是时间跳转
      return processVolumeData(inputTime, false, false, true);
    }

    // 图↔格联动：图表（C2/C5）点击某点 → 统一走 clickMoveTime 跳转到该时刻
    // 与顶栏「跳转」按钮同一条路径，保证所有导航入口行为一致（修改计划.md D6）
    const handleChartSeek = (timeStr) => {
      if (!timeStr) return;
      selectTime.value = timeStr;
      clickMoveTime(timeStr);
    };

    // 将档位 orders 数组转换为 VolumeTable 表格数据格式（共享实现，
    // 供 processVolumeData / updateVolumeDataFromSnapshot / moveticks 使用）
    const transformOrdersToTableData = (orders, levelPrice) => {
      if (!orders || orders.length === 0) {
        return { orders: [], data: [[], []] };
      }

      // 创建包含所有订单数据的行，不限制数量
      const row = {};

      // 将订单数据填入对应的格子中，每个订单占用一个格子，键连续为 v1..vN
      orders.forEach((order, index) => {
        const colKey = `v${index + 1}`;
        row[colKey] = order.remaining_volume || '';
        // 将订单详细信息作为属性存储
        row[`${colKey}_order_id`] = order.order_id || '';
        row[`${colKey}_order_local_id`] = order.order_local_id || '';
        row[`${colKey}_direction`] = order.direction || '';
        row[`${colKey}_price`] = levelPrice || order.price || '';
        row[`${colKey}_create_time`] = order.create_time || '';
      });

      return {
        orders: orders, // 保存原始订单数据用于搜索
        data: [[row], [row]] // VolumeDataTable期望的格式是二维数组，[0]用于普通模式，[1]用于全屏模式
      };
    };

    const processVolumeData = (timeValue, skipProgressUpdate = false, isInitialLoad = false, isTimeJump = false) => {
      loading.value = true
      // 记录跳转前位置，吸附时用于计算实际位移（弹窗展示）
      const prevTime = selectTime.value;
      const prevOrderIndex = orderIndex.value;
      let params = {
        sym: selectSym.value,
        date: selectDate.value,
        time: timeValue
      }
      return getVolumeDataByTime(params).then((res) => {
        if (res.code == 0 && res.data && res.data.snapshot) {
          // 更新 isETF 状态
          isETF.value = res.data.is_ETF || false;

          // 处理volumeData - 从snapshot中提取需要的数据
          const snapshot = res.data.snapshot;
          const levels = snapshot.levels || {};

          // 构建volumeData格式
          volumeData.value = {
            // 使用快照实际时间，保证右侧当前点与左侧表格属于同一时刻。
            datetime: snapshot.timestamp || res.data.time || '',
            is_ETF: isETF.value,
            ask1: transformOrdersToTableData(levels.ask1?.orders || [], levels.ask1?.price),
            ask1_price: levels.ask1?.price || 0,
            ask2: transformOrdersToTableData(levels.ask2?.orders || [], levels.ask2?.price),
            ask2_price: levels.ask2?.price || 0,
            ask3: transformOrdersToTableData(levels.ask3?.orders || [], levels.ask3?.price),
            ask3_price: levels.ask3?.price || 0,
            bid1: transformOrdersToTableData(levels.bid1?.orders || [], levels.bid1?.price),
            bid1_price: levels.bid1?.price || 0,
            bid2: transformOrdersToTableData(levels.bid2?.orders || [], levels.bid2?.price),
            bid2_price: levels.bid2?.price || 0,
            bid3: transformOrdersToTableData(levels.bid3?.orders || [], levels.bid3?.price),
            bid3_price: levels.bid3?.price || 0
          };

          // 处理levelDataArray
          const levelDataArray = [];
          Object.keys(levels).forEach(levelKey => {
            if (levels[levelKey]) {
              const levelData = levels[levelKey];
              const total_volume = levelData.total_volume || 0;
              const order_count = levelData.order_count || 0;

              // 将英文档位转换为中文
              let chineseLevel = levelKey;
              if (levelKey === 'bid1') chineseLevel = '买一';
              else if (levelKey === 'bid2') chineseLevel = '买二';
              else if (levelKey === 'bid3') chineseLevel = '买三';
              else if (levelKey === 'ask1') chineseLevel = '卖一';
              else if (levelKey === 'ask2') chineseLevel = '卖二';
              else if (levelKey === 'ask3') chineseLevel = '卖三';

              levelDataArray.push({
                level: chineseLevel,
                volume: total_volume,
                number: order_count
              });
            }
          });
          levelData.value = levelDataArray;

          // 保持 tradeData 的 level 列不变，不重新生成数组
          // tradeData 将通过 updateMarketData() 函数单独更新数据列，level 列保持固定值

          // 更新时间和订单索引 - 优先使用snapshot中的timestamp（确保时间格式为HH:mm:ss.SSS）
          const rawTime = snapshot.timestamp || res.data.time || timeValue;
          const extractedTime = extractTimeFromTimestamp(rawTime);

          // 只有当提取的时间有效时才更新selectTime，避免时间被清空
          if (extractedTime && extractedTime.trim() !== '') {
            let finalTime = extractedTime;

            if (isInitialLoad) {
              finalTime = timeValue;
            }

            // 应用边界检测
            const checkResult = checkTimeBoundary(finalTime);
            selectTime.value = checkResult.corrected;
            localStorage.setItem('volumeQueue_timestamp', checkResult.corrected);

            // 时间跳转时，若实际快照时间与输入时间不一致，提示用户并附实际位移
            if (isTimeJump && timeValue && extractedTime && extractedTime !== timeValue) {
              createMessage.warning(`目标时间 ${timeValue} 无对应快照，已跳转到 ${extractedTime}${formatJumpShift(prevTime, prevOrderIndex, extractedTime, snapshot.snapshot_id || res.data.orderindex)}`);
            }
          }

          // 从snapshot中提取snapshot_id更新orderIndex
          orderIndex.value = snapshot.snapshot_id || res.data.orderindex || 0;
          // 从snapshot中提取change_index更新changeIndex
          changeIndex.value = snapshot.change_index || 0;
          // 从snapshot中提取snapshot_id更新snapshotId
          snapshotId.value = snapshot.snapshot_id || 0;
          localStorage.setItem('volumeQueue_orderIndex', (snapshot.snapshot_id || res.data.orderindex || 0).toString());
          messageText.value = '查询成功'
          
          // 如果订单锁定进度条正在显示，更新进度条信息；否则显示独立通知
          if (lockProgress.value.show) {
            lockProgress.value.detail = res.message || '快照数据加载成功';
            updateLockProgressNotification();
          } else {
            // 改进：显示实际跳转的时间，而不是后端返回的消息
            if (isTimeJump) {
              const displayTimeStr = selectTime.value || timeValue;
              createMessage.success(`已跳转到时间 ${displayTimeStr}`);
            } else {
              createMessage.success(res.message || '数据加载成功');
            }
          }

          // 更新市场数据
          updateMarketData();

          // 数据加载成功，停止WebSocket监听
          if (progressWebSocket) {
            progressWebSocket.stopListening();
            progressWebSocket = null;
          }

          // 更新进度条状态并隐藏（只有在显示进度时才执行）
          if (!skipProgressUpdate && initProgress.value.show) {
            initProgress.value.progress = 100;
            initProgress.value.message = '数据加载完成';
            initProgress.value.status = 'completed';

            // 更新为完成状态
            updateProgressNotification();

            setTimeout(() => {
              closeProgressNotification();
              initProgress.value.show = false;
            }, 1500);
          }

        } else {
          messageText.value = res.message || '查询失败'
          createMessage.error(res.message || '查询数据失败')

          // 数据加载失败，也停止WebSocket监听
          if (progressWebSocket) {
            progressWebSocket.stopListening();
            progressWebSocket = null;
          }

          // 隐藏进度条（只有在显示进度时才执行）
          if (!skipProgressUpdate && initProgress.value.show) {
            closeProgressNotification();
            initProgress.value.show = false;
          }
        }
        loading.value = false
      }).catch((error) => {
        loading.value = false
        createMessage.error('网络请求失败，请检查网络连接')

        // 网络请求失败，也停止WebSocket监听
        if (progressWebSocket) {
          progressWebSocket.stopListening();
          progressWebSocket = null;
        }

        // 隐藏进度条（只有在显示进度时才执行）
        if (!skipProgressUpdate && initProgress.value.show) {
          closeProgressNotification();
          initProgress.value.show = false;
        }
      })
    }

    const getOrderbookData = async () => {
      // 用户可能输入了完整日期但未点选下拉选项就点「开始」：先同步未提交的输入，
      // 避免 blur 的 100ms 延迟让请求仍带旧日期（加载成旧日期的数据）
      const pendingDate = (dateLastValidInput.value || dateSearchValue.value || '').trim();
      if (pendingDate) {
        const exactDate = datesData.value.find(option =>
          option.value.toLowerCase() === pendingDate.toLowerCase()
        );
        if (exactDate && exactDate.value !== selectDate.value) {
          selectDateItem(exactDate.value);
        } else if (!exactDate) {
          createMessage.warning(`输入的日期 "${pendingDate}" 未完全匹配，将使用当前选中的 ${selectDate.value || '（空）'}`);
        }
      }

      // 首先调用初始化任务API
      try {
        initProgress.value.show = true;
        initProgress.value.message = '正在初始化TradeBook...';
        initProgress.value.progress = 0;

        // 创建进度条通知
        createProgressNotification();

        const initParams = {
          sym: selectSym.value,
          date: selectDate.value
        };

        const initResult = await initTradeBook(initParams);

        if (initResult.code === 0) {
          if (initResult.data.status === 'ready') {
            // TradeBook已存在且可用，直接进行数据加载
            createMessage.success(initResult.data.message || 'TradeBook已准备就绪');
            closeProgressNotification();
            initProgress.value.show = false;

            // 直接开始数据加载，不显示初始化进度
            proceedWithDataLoading(true);
          } else if (initResult.data.status === 'initializing') {
            // 需要初始化，显示进度
            initProgress.value.taskId = initResult.data.task_id;
            initProgress.value.message = initResult.data.message || '正在初始化...';
            initProgress.value.progress = 0;
            initProgress.value.status = 'initializing';
            initProgress.value.show = true;

            // 更新进度条通知
            updateProgressNotification();

            createMessage.info('初始化任务已创建，正在监听进度...');

            // 启动WebSocket进度监听
            await startProgressListening(initResult.data.task_id);
          }
        } else {
          createMessage.error(initResult.message || '初始化任务失败');
          closeProgressNotification();
          initProgress.value.show = false;
          return; // 初始化失败时不继续执行后续逻辑
        }
      } catch (error) {
        console.error('❌ 初始化TradeBook错误详情:', {
          error: error.message || error,
          stack: error.stack,
          params: initParams,
          timestamp: new Date().toISOString()
        });

        let errorMessage = "初始化TradeBook失败";
        if (error.message && (error.message.includes("场外基金") || error.message.includes("没有可用的盘口数据"))) {
          errorMessage = error.message;
        } else if (error.message?.includes("NetworkError") ||
            error.message?.includes("fetch") ||
            error.message?.includes("Connection refused")) {
          errorMessage = "无法连接到服务器，请检查服务器状态";
        } else if (error.message?.includes("timeout")) {
          errorMessage = "请求超时，服务器可能正在处理大量数据";
        } else if (error.message) {
          errorMessage = `初始化失败: ${error.message}`;
        }

        createMessage.error(errorMessage);
        closeProgressNotification();
        initProgress.value.show = false;
        return; // 初始化失败时不继续执行后续逻辑
      }
    };

    // 分离的数据加载逻辑
    const proceedWithDataLoading = (skipProgressUpdate = false) => {

      // 开始按钮默认使用9:30:00.000时间戳
      const defaultTime = '09:30:00.000';
      selectTime.value = defaultTime;

      // 持久化更新的默认时间
      localStorage.setItem('volumeQueue_timestamp', defaultTime);

      // 只有在需要时才更新进度条消息
      if (!skipProgressUpdate && initProgress.value.show) {
        initProgress.value.message = '正在加载数据...';
        updateProgressNotification();
      }

      processVolumeData(defaultTime, skipProgressUpdate, true); // 传递第三个参数表示是初始化调用

      // 市场数据将通过 processVolumeData() 中调用的 updateMarketData() 函数自动获取和更新
    }



    const getStaticData = () => {
      loading.value = true
      getSymList().then((res) => {
        loading.value = false
        if (res.code == 0) {
          symsData.value = []
          res.data.forEach(item => {
            symsData.value.push(toSymbolOption(item))
          })
          if (res.message) {
            createMessage.success(res.message)
          }
        } else {
            messageText.value = res.message
            createMessage.error(res.message || '获取股票列表失败')
          }
      }).catch((error) => {
        loading.value = false
        createMessage.error('网络请求失败，请检查网络连接')
      })
    }

    getStaticData()

    // A2：通过 next_change 接口实现高效步进，每次最多 2 个请求
    // 第 1 个请求查目标时间快照；若快照时间戳与当前相同（区间内无变化），
    // 第 2 个请求用 next_change 直接取相邻的下一个/上一个变化点。
    // 跳转位移描述：从「跳转前位置」到「实际落点」的时间差与订单数差。
    // 仅在发生吸附/偏移（实际落点 ≠ 请求目标）时追加到弹窗文案尾部。
    const formatJumpShift = (prevTime, prevOrderIndex, landedTime, landedOrderIndex) => {
      const parts = [];
      // 注意 parseTimeToMs 对非法输入返回 0 而非 NaN，必须先校验非空
      if (prevTime && landedTime) {
        const diffMs = Math.abs(parseTimeToMs(landedTime) - parseTimeToMs(prevTime));
        const timeDesc = diffMs >= 1000 ? `${Number((diffMs / 1000).toFixed(3))}秒` : `${diffMs}毫秒`;
        parts.push(`本次共跳转 ${timeDesc}`);
      }
      const orderDiff = Math.abs(Number(landedOrderIndex || 0) - Number(prevOrderIndex || 0));
      parts.push(`跨过 ${orderDiff} 个订单`);
      return `（${parts.join('，')}）`;
    };

    const moveTimes = async (intevel) => {
      const currentTime = selectTime.value;
      const prevOrderIndex = orderIndex.value;
      let totalMs = parseTimeToMs(currentTime) + intevel;

      // 确保时间在交易时间范围内 (09:30:00.000 - 15:00:00.000)
      const minTime = 9 * 3600000 + 30 * 60000; // 09:30:00.000
      const maxTime = 15 * 3600000; // 15:00:00.000

      if (totalMs < minTime) totalMs = minTime;
      if (totalMs > maxTime) totalMs = maxTime;

      const newTime = formatMsToTimeStr(totalMs);

      // 更新时间
      selectTime.value = newTime;
      localStorage.setItem('volumeQueue_timestamp', newTime);

      loading.value = true;

      const direction = intevel > 0 ? 1 : -1;
      const moveDesc = `${direction > 0 ? '向前' : '向后'}移动${Math.abs(intevel) >= 1000 ? `${Math.abs(intevel) / 1000}秒` : `${Math.abs(intevel)}毫秒`}`;

      try {
        // 第 1 个请求：目标时间处的快照
        const res = await getVolumeDataByTime({ sym: selectSym.value, date: selectDate.value, time: newTime });

        if (res.code == 0 && res.data && res.data.snapshot) {
          const snapshot = res.data.snapshot;
          const returnedTime = extractTimeFromTimestamp(snapshot.timestamp || res.data.time || '');

          if (returnedTime !== currentTime) {
            // 目标区间内有变化，直接使用该快照
            updateVolumeDataFromSnapshot(snapshot, res.data);
            updateMarketData();

            if (returnedTime !== newTime) {
              createMessage.info(`目标时间 ${newTime} 无新快照，已自动跳转到 ${returnedTime}${formatJumpShift(currentTime, prevOrderIndex, returnedTime, orderIndex.value)}`);
            } else {
              createMessage.success(`时间移动成功：${moveDesc}`);
            }
            return;
          }

          // 第 2 个请求：目标时间处仍是当前快照（区间内无变化）→ 取相邻变化点
          const nc = await getNextChange({ sym: selectSym.value, date: selectDate.value, time: newTime, direction });

          if (nc.code == 0 && nc.data && nc.data.snapshot) {
            updateVolumeDataFromSnapshot(nc.data.snapshot, nc.data);
            updateMarketData();
            const ncTime = extractTimeFromTimestamp(nc.data.snapshot.timestamp || '');
            createMessage.info(`${newTime} 附近无变化，已跳转到${direction > 0 ? '下一个' : '上一个'}变化点 ${ncTime}${formatJumpShift(currentTime, prevOrderIndex, ncTime, orderIndex.value)}`);
            return;
          }

          // 当天没有更多变化：恢复原时间显示
          createMessage.warning(nc.message || `已到达当天${direction > 0 ? '最后' : '最早'}一个变化点，无法继续移动`);
          selectTime.value = currentTime;
          localStorage.setItem('volumeQueue_timestamp', currentTime);
        } else {
          createMessage.error(res.message || '查询数据失败');
          selectTime.value = currentTime;
          localStorage.setItem('volumeQueue_timestamp', currentTime);
        }
      } catch (error) {
        createMessage.error('网络请求失败，请检查网络连接');
        selectTime.value = currentTime;
        localStorage.setItem('volumeQueue_timestamp', currentTime);
      } finally {
        loading.value = false;
      }
    }


    // 辅助函数：从snapshot更新volumeData（提取公共逻辑）
    const updateVolumeDataFromSnapshot = (snapshot, responseData) => {
      const levels = snapshot.levels || {};

      // 构建volumeData格式
      volumeData.value = {
        datetime: snapshot.timestamp || responseData.time || '',
        is_ETF: responseData.is_ETF !== undefined ? responseData.is_ETF : isETF.value,
        ask1: transformOrdersToTableData(levels.ask1?.orders || [], levels.ask1?.price),
        ask1_price: levels.ask1?.price || 0,
        ask2: transformOrdersToTableData(levels.ask2?.orders || [], levels.ask2?.price),
        ask2_price: levels.ask2?.price || 0,
        ask3: transformOrdersToTableData(levels.ask3?.orders || [], levels.ask3?.price),
        ask3_price: levels.ask3?.price || 0,
        bid1: transformOrdersToTableData(levels.bid1?.orders || [], levels.bid1?.price),
        bid1_price: levels.bid1?.price || 0,
        bid2: transformOrdersToTableData(levels.bid2?.orders || [], levels.bid2?.price),
        bid2_price: levels.bid2?.price || 0,
        bid3: transformOrdersToTableData(levels.bid3?.orders || [], levels.bid3?.price),
        bid3_price: levels.bid3?.price || 0
      };

      // 处理levelDataArray
      const levelDataArray = [];
      Object.keys(levels).forEach(levelKey => {
        if (levels[levelKey]) {
          const levelData = levels[levelKey];
          const total_volume = levelData.total_volume || 0;
          const order_count = levelData.order_count || 0;

          let chineseLevel = levelKey;
          if (levelKey === 'bid1') chineseLevel = '买一';
          else if (levelKey === 'bid2') chineseLevel = '买二';
          else if (levelKey === 'bid3') chineseLevel = '买三';
          else if (levelKey === 'ask1') chineseLevel = '卖一';
          else if (levelKey === 'ask2') chineseLevel = '卖二';
          else if (levelKey === 'ask3') chineseLevel = '卖三';

          levelDataArray.push({
            level: chineseLevel,
            volume: total_volume,
            number: order_count
          });
        }
      });
      levelData.value = levelDataArray;

      // 保持 tradeData 的 level 列不变，不重新生成数组
      // tradeData 将通过 updateMarketData() 函数单独更新数据列，level 列保持固定值

      // 更新时间和订单索引（确保时间格式为HH:mm:ss.SSS）
      const rawTime = snapshot.timestamp || responseData.time || '';
      const extractedTime = extractTimeFromTimestamp(rawTime);

      // 只有当提取的时间有效时才更新selectTime，避免边界情况下时间被清空
      if (extractedTime && extractedTime.trim() !== '') {
        // 应用边界检测
        const checkResult = checkTimeBoundary(extractedTime);
        selectTime.value = checkResult.corrected;
        // 持久化更新的值
        localStorage.setItem('volumeQueue_timestamp', checkResult.corrected);
      }

      orderIndex.value = snapshot.snapshot_id || responseData.orderindex || 0;
      changeIndex.value = snapshot.change_index || 0;
      snapshotId.value = snapshot.snapshot_id || 0;

      localStorage.setItem('volumeQueue_orderIndex', orderIndex.value.toString());
    }


    // 判断订单是否已经出现在当前快照中。订单 ID 可能由接口分别以数字或字符串返回，统一按字符串比较。
    const isOrderVisibleInCurrentSnapshot = (orderId) => {
      const normalizedId = String(orderId ?? '').trim();
      if (!normalizedId) return false;

      for (const key of ['ask1', 'ask2', 'ask3', 'bid1', 'bid2', 'bid3']) {
        const rowData = volumeData.value[key]?.data?.[0]?.[0];
        if (!rowData) continue;

        for (let i = 1; i <= 10000; i += 1) {
          const volumeKey = 'v' + i;
          if (rowData[volumeKey] === undefined || rowData[volumeKey] === '') break;
          const currentId = rowData[volumeKey + '_order_local_id'];
          if (currentId !== undefined && currentId !== null && String(currentId).trim() === normalizedId) {
            return true;
          }
        }
      }
      return false;
    };

    // 创建时间对应的快照可能早于订单真正写入盘口的快照。
    // 从创建时间开始沿变化点向前查找，直到当前快照包含订单。
    const findFirstSnapshotContainingOrder = async (orderId, fromTime) => {
      if (isOrderVisibleInCurrentSnapshot(orderId)) return true;

      let cursorTime = fromTime;
      const maxAttempts = 100;
      for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
        let response;
        try {
          response = await getNextChange({
            sym: selectSym.value,
            date: selectDate.value,
            time: cursorTime,
            direction: 1,
          });
        } catch (error) {
          debugLog('查找订单进入盘口的下一变化快照失败:', error);
          return false;
        }

        const snapshot = response?.code === 0 ? response.data?.snapshot : null;
        if (!snapshot) return false;

        const nextTime = extractTimeFromTimestamp(snapshot.timestamp || response.data?.time || '');
        if (!nextTime || parseTimeToMs(nextTime) <= parseTimeToMs(cursorTime)) return false;

        updateVolumeDataFromSnapshot(snapshot, response.data);
        updateMarketData();
        cursorTime = nextTime;

        if (isOrderVisibleInCurrentSnapshot(orderId)) {
          debugLog('订单 ' + orderId + ' 已在 ' + nextTime + ' 的快照中找到');
          return true;
        }
      }

      debugLog('向前检查 ' + maxAttempts + ' 个变化快照后仍未找到订单 ' + orderId);
      return false;
    };

    const moveticks = (intevel) => {
      const prevTime = selectTime.value;
      const prevOrderIndex = orderIndex.value;
      const prevChangeIndex = changeIndex.value;
      let newChangeIndex = changeIndex.value + intevel
      loading.value = true
      let params = {
        sym: selectSym.value,
        date: selectDate.value,
        index: newChangeIndex  // 使用index参数传递change_index
      }

      getSnapshotByIndex(params).then((res) => {
        if (res.code == 0 && res.data && res.data.snapshot) {
          // 使用与processVolumeData相同的数据处理逻辑
          const snapshot = res.data.snapshot;
          const levels = snapshot.levels || {};

          // 构建volumeData格式（与processVolumeData相同）
          volumeData.value = {
            datetime: snapshot.timestamp || res.data.time || '',
            is_ETF: res.data.is_ETF !== undefined ? res.data.is_ETF : isETF.value,
            ask1: transformOrdersToTableData(levels.ask1?.orders || [], levels.ask1?.price),
            ask1_price: levels.ask1?.price || 0,
            ask2: transformOrdersToTableData(levels.ask2?.orders || [], levels.ask2?.price),
            ask2_price: levels.ask2?.price || 0,
            ask3: transformOrdersToTableData(levels.ask3?.orders || [], levels.ask3?.price),
            ask3_price: levels.ask3?.price || 0,
            bid1: transformOrdersToTableData(levels.bid1?.orders || [], levels.bid1?.price),
            bid1_price: levels.bid1?.price || 0,
            bid2: transformOrdersToTableData(levels.bid2?.orders || [], levels.bid2?.price),
            bid2_price: levels.bid2?.price || 0,
            bid3: transformOrdersToTableData(levels.bid3?.orders || [], levels.bid3?.price),
            bid3_price: levels.bid3?.price || 0
          };

          // 处理levelDataArray
          const levelDataArray = [];
          Object.keys(levels).forEach(levelKey => {
            if (levels[levelKey]) {
              const levelData = levels[levelKey];
              const total_volume = levelData.total_volume || 0;
              const order_count = levelData.order_count || 0;

              // 将英文档位转换为中文
              let chineseLevel = levelKey;
              if (levelKey === 'bid1') chineseLevel = '买一';
              else if (levelKey === 'bid2') chineseLevel = '买二';
              else if (levelKey === 'bid3') chineseLevel = '买三';
              else if (levelKey === 'ask1') chineseLevel = '卖一';
              else if (levelKey === 'ask2') chineseLevel = '卖二';
              else if (levelKey === 'ask3') chineseLevel = '卖三';

              levelDataArray.push({
                level: chineseLevel,
                volume: total_volume,
                number: order_count
              });
            }
          });
          levelData.value = levelDataArray;

          // 保持 tradeData 的 level 列不变，不重新生成数组
          // tradeData 将通过 updateMarketData() 函数单独更新数据列，level 列保持固定值

          // 更新时间和订单索引 - 优先使用snapshot中的timestamp（确保时间格式为HH:mm:ss.SSS）
          const rawTime = snapshot.timestamp || res.data.time || '';
          const extractedTime = extractTimeFromTimestamp(rawTime);

          // 只有当提取的时间有效时才更新selectTime，避免时间被清空
          if (extractedTime && extractedTime.trim() !== '') {
            // 应用边界检测
            const checkResult = checkTimeBoundary(extractedTime);
            selectTime.value = checkResult.corrected;
            localStorage.setItem('volumeQueue_timestamp', checkResult.corrected);
          }

          // 更新change_index：以快照实际返回的为准（引擎对越界索引会钳到边界快照，
          // 此时发生吸附，弹窗需展示实际位移而不是请求步数）
          const returnedChangeIndex = snapshot.change_index != null ? snapshot.change_index : newChangeIndex;
          changeIndex.value = returnedChangeIndex;
          // 从snapshot中提取orderIndex和snapshotId
          orderIndex.value = snapshot.snapshot_id || res.data.orderindex || 0;
          snapshotId.value = snapshot.snapshot_id || 0;
          localStorage.setItem('volumeQueue_orderIndex', orderIndex.value.toString());

          // 计算移动方向和数量
          const direction = intevel > 0 ? '向前' : '向后';
          if (returnedChangeIndex !== newChangeIndex) {
            const actualCount = Math.abs(returnedChangeIndex - prevChangeIndex);
            createMessage.info(
              `已到达当天${intevel > 0 ? '最后' : '最早'}一个变化点，实际${direction}移动${actualCount}个订单至 ${selectTime.value}` +
              formatJumpShift(prevTime, prevOrderIndex, selectTime.value, orderIndex.value),
            );
          } else {
            const count = Math.abs(intevel);
            createMessage.success(`订单移动成功：${direction}移动${count}个订单`);
          }

          // 更新市场数据
          updateMarketData();
        } else {
          createMessage.error(res.message || '移动订单失败');
        }
        loading.value = false;
      }).catch((error) => {
        loading.value = false;
        createMessage.error('网络请求失败，请检查网络连接');
      });
    }

    const isTableVisible = (tableKey) => !expandedTable.value || expandedTable.value === tableKey;

    const showHideTable = (tableKey) => {
      expandedTable.value = expandedTable.value === tableKey ? null : tableKey;
    };

    // 按数量锁定订单逻辑函数：收集所有符合的订单，弹出选择器让用户勾选（B1）
    const lockSelectorVisible = ref(false);
    const lockCandidates = ref([]);


    const LOCK_LEVEL_DEFS = [
      { key: 'ask1', label: '卖一', direction: '卖' },
      { key: 'ask2', label: '卖二', direction: '卖' },
      { key: 'ask3', label: '卖三', direction: '卖' },
      { key: 'bid1', label: '买一', direction: '买' },
      { key: 'bid2', label: '买二', direction: '买' },
      { key: 'bid3', label: '买三', direction: '买' },
    ];

    // 顶栏锁定订单列表：订单离开当前快照后仍保留 ID，并明确提示当前不可见。
    const lockedOrderDetails = computed(() => {
      const lockedSet = new Set(lockedOrderIds.value.map((id) => String(id)));
      const details = new Map();

      for (const levelDef of LOCK_LEVEL_DEFS) {
        const rowData = volumeData.value[levelDef.key]?.data?.[0]?.[0];
        if (!rowData) continue;

        for (let i = 1; ; i += 1) {
          const volumeKey = 'v' + i;
          if (rowData[volumeKey] === undefined || rowData[volumeKey] === '') break;

          const id = rowData[volumeKey + '_order_local_id'];
          if (id === undefined || id === null || !lockedSet.has(String(id))) continue;

          details.set(String(id), {
            id: String(id),
            visible: true,
            levelLabel: levelDef.label,
            direction: levelDef.direction,
            price: volumeData.value[levelDef.key + '_price'],
            volume: rowData[volumeKey],
          });
        }
      }

      return lockedOrderIds.value.map((id) => details.get(String(id)) || {
        id: String(id),
        visible: false,
      });
    });

    const formatLockedPrice = (price) => {
      if (price === undefined || price === null || price === '') return '--';
      return (Number(price) / 10000).toFixed(isETF.value ? 3 : 2);
    };

    const lockOrdersByVolume = () => {
       if (!lockByVolumeValue.value.trim()) {
         createMessage.warning('请输入remaining volume数值');
         return;
       }

       const searchNum = parseFloat(lockByVolumeValue.value.trim());
       if (isNaN(searchNum)) {
         createMessage.warning('请输入有效的数字');
         return;
       }

       const candidates = [];

       // 遍历六档队列，收集所有剩余量匹配的订单（v 键连续，遇到空缺即停）
       for (const levelDef of LOCK_LEVEL_DEFS) {
         const data = volumeData.value[levelDef.key];
         const rowData = data && data.data && data.data[0] && data.data[0][0];
         if (!rowData) continue;
         for (let i = 1; ; i++) {
           const volumeKey = `v${i}`;
           if (rowData[volumeKey] === undefined || rowData[volumeKey] === '') break;
           const orderLocalId = rowData[`${volumeKey}_order_local_id`];
           if (parseFloat(rowData[volumeKey]) === searchNum && orderLocalId) {
             candidates.push({
               order_local_id: String(orderLocalId),
               order_id: rowData[`${volumeKey}_order_id`] || '',
               volume: rowData[volumeKey],
               price: volumeData.value[`${levelDef.key}_price`] || '',
               levelLabel: levelDef.label,
               direction: levelDef.direction,
               create_time: rowData[`${volumeKey}_create_time`] || '',
             });
           }
         }
       }

       if (candidates.length === 0) {
         createMessage.info('未找到匹配的订单');
         return;
       }

       // 弹出选择器，由用户勾选要锁定的订单
       lockCandidates.value = candidates;
       lockSelectorVisible.value = true;
     };

    // 选择器确认：批量锁定勾选的订单
    const handleLockConfirm = (ids) => {
      const merged = [...new Set([...lockedOrderIds.value, ...ids])];
      lockedOrderIds.value = merged;
      createMessage.success(`成功锁定 ${ids.length} 个订单`);
    };

     // 按ID锁定订单逻辑函数（增强版，支持自动导航）
      const lockOrdersById = (enableAutoScroll = false) => {
        debugLog('🔒 [lockOrdersById] ========== 开始执行 ==========');
        debugLog('   📋 参数信息:');
        debugLog('      - 输入框值 (lockByIdValue.value):', lockByIdValue.value);
        debugLog('      - 自动导航 (enableAutoScroll):', enableAutoScroll);
        debugLog('      - volumeData 键数量:', Object.keys(volumeData.value).length);
        debugLog('      - 当前已锁定订单数:', lockedOrderIds.value.length);
        
        const searchId = lockByIdValue.value;
        
        if (!searchId || !searchId.trim()) {
          debugLog('❌ [lockOrdersById] 订单ID为空，无法执行锁定');
          createMessage.warning('请输入订单ID');
          return false;
        }

        const finalSearchId = searchId.trim();
        debugLog('🔍 [lockOrdersById] 开始搜索订单ID:', finalSearchId);
        debugLog('   📊 volumeData 结构:');
        Object.keys(volumeData.value).forEach(key => {
          if (key.includes('ask') || key.includes('bid')) {
            const data = volumeData.value[key];
            const hasData = data && data.data && data.data[0] && data.data[0][0];
            debugLog(`      - ${key}: ${hasData ? '有数据' : '无数据'}`);
          }
        });
        
        let found = false;
        let foundInTable = null;
        let foundInColumn = null;

        // 遍历所有volumeData中的订单数据
        outerLoop: for (const key of Object.keys(volumeData.value)) {
          if (key.includes('ask') || key.includes('bid')) {
            const data = volumeData.value[key];
            if (data && data.data && data.data[0] && data.data[0][0]) {
              const rowData = data.data[0][0];
              debugLog(`   🔎 检查表格: ${key}`);
              
              // 遍历所有v1, v2, v3...列
               for (let i = 1; i <= 10000; i++) {
                 const orderLocalIdKey = `v${i}_order_local_id`;
                 const orderLocalId = rowData[orderLocalIdKey];
                 
                 // 只在前10列输出详细日志，避免日志过多
                 if (i <= 10 && orderLocalId) {
                   debugLog(`      - 列 ${i}: order_local_id = ${orderLocalId}`);
                 }
                 
                 if (orderLocalId !== undefined && orderLocalId !== null && String(orderLocalId).trim() === finalSearchId) {
                   debugLog('✅ [lockOrdersById] 找到匹配的订单!');
                   debugLog(`   📍 位置信息:`);
                   debugLog(`      - 表格: ${key}`);
                   debugLog(`      - 列索引: ${i}`);
                   debugLog(`      - order_local_id: ${orderLocalId}`);
                   debugLog(`      - remaining_volume: ${rowData[`v${i}`]}`);
                   
                   foundInTable = key;
                   foundInColumn = i;
                   
                   // 直接锁定匹配的order_local_id
                   if (!lockedOrderIds.value.includes(finalSearchId)) {
                     lockedOrderIds.value.push(finalSearchId);
                     debugLog('   ✓ 订单已添加到锁定列表');
                     debugLog('   📋 当前锁定列表:', lockedOrderIds.value);
                   } else {
                     debugLog('   ℹ️ 订单已在锁定列表中');
                   }
                   found = true;
                   break outerLoop;
                 }
               }
            } else {
              debugLog(`   ⚠️ 表格 ${key} 无数据或结构不正确`);
            }
          }
        }

        if (found) {
          debugLog('✅ [lockOrdersById] 订单锁定成功');
          debugLog(`   📊 锁定结果:`);
          debugLog(`      - 找到位置: ${foundInTable} 列 ${foundInColumn}`);
          debugLog(`      - 已锁定订单总数: ${lockedOrderIds.value.length}`);
          createMessage.success('成功锁定订单');
          
          // 如果启用自动导航，则滚动到订单位置
          if (enableAutoScroll) {
            debugLog('📍 [lockOrdersById] 启用自动导航，准备滚动到订单位置');
            setTimeout(() => {
              scrollToLockedOrder(finalSearchId);
            }, 300);
          } else {
            debugLog('ℹ️ [lockOrdersById] 未启用自动导航');
          }
        } else {
          debugLog('❌ [lockOrdersById] 未找到匹配的订单');
          debugLog('   🔍 搜索的订单ID:', finalSearchId);
          debugLog('   📊 已搜索的表格:', Object.keys(volumeData.value).filter(k => k.includes('ask') || k.includes('bid')));
          createMessage.info('未找到匹配的订单ID');
        }
        
        debugLog('🔒 [lockOrdersById] ========== 执行结束 ==========');
        debugLog('   📊 最终状态:');
        debugLog('      - 是否找到: ', found);
        debugLog('      - 已锁定订单数:', lockedOrderIds.value.length);
        
        return found;
       };

     const confidenceLabels = {
       high: '高',
       medium: '中',
       low: '低',
     };

     const executionEstimateTitle = computed(() => {
       const id = executionEstimateOrder.value?.order_local_id;
       return id ? '订单 ' + id + ' 成交时间分析' : '订单成交时间分析';
     });

     // 弹窗内「买1/卖1」标签随订单方向切换
     const estimateSideLabel = computed(() =>
       executionEstimateData.value?.order?.side === -1 ? '卖' : '买',
     );

     const formatExecutionTime = (value) => {
       if (!value) return '--';
       return String(value).split(' ').pop() || '--';
     };

     const formatEstimateVolume = (value) => {
       const numeric = Number(value);
       if (!Number.isFinite(numeric)) return '--';
       return numeric.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
     };

     const formatEstimateRate = (value) => {
       const numeric = Number(value);
       if (!Number.isFinite(numeric) || numeric <= 0) return '--';
       return numeric.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' 手/秒';
     };

     // 分窗口表格的速率格式化：0 与负值（队列变长）都是有效信息，不能显示 '--'
     const formatWindowRate = (value) => {
       const numeric = Number(value);
       if (!Number.isFinite(numeric)) return '--';
       return numeric.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
     };

     // 窗口得分悬浮的细分表：指标列只列名称+满分，得分列=原始数据 + 加权后得分
     const formatPartScore = (value) => {
       const numeric = Number(value);
       if (!Number.isFinite(numeric)) return '--';
       return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1);
     };
     const scoreTipRows = (row) => {
       const p = row?.score_parts || {};
       const elapsed = Math.round(Number(row?.elapsed_seconds) || 0);
       const waitMin = (Number(row?.first_fill_wait_ms) || 0) / 60000;
       const r = Number(p.r);
       const q = Number(p.q);
       return [
         { name: '样本量', max: 20, raw: `成交 ${row?.trade_count ?? 0} 笔`, score: formatPartScore((p.s1 ?? 0) * 20) },
         {
           name: '稳定性', max: 30,
           raw: p.dev == null ? '仅单窗，按中性计' : `偏离各窗均值 ${(p.dev * 100).toFixed(0)}%`,
           score: formatPartScore((p.s2 ?? 0) * 30),
         },
         {
           name: '外推距离', max: 25,
           raw: Number.isFinite(r) ? `等待 ${waitMin.toFixed(1)} 分钟 ≈ ${r.toFixed(1)} 倍观察` : '--',
           score: formatPartScore((p.s3 ?? 0) * 25),
         },
         {
           name: '净速率显著性', max: 15,
           raw: Number.isFinite(q) ? `净流量占 ${(q * 100).toFixed(0)}%` : '--',
           score: formatPartScore((p.s4 ?? 0) * 15),
         },
         { name: '观察时长', max: 10, raw: `${elapsed} 秒`, score: formatPartScore((p.s5 ?? 0) * 10) },
       ];
     };

     // 仅三档订单的窗口行携带买2/卖2净速度列
     const hasSecondLevelNet = computed(() =>
       (executionEstimateData.value?.prediction?.windows || []).some(
         (row) => row.second_net_rate !== null && row.second_net_rate !== undefined,
       ),
     );

     const formatEstimateWindow = (value) => {
       const ms = Number(value);
       if (!Number.isFinite(ms) || ms <= 0) return '--';
       if (ms >= 60000) return (ms / 60000).toFixed(ms % 60000 === 0 ? 0 : 1) + ' 分钟';
       return (ms / 1000).toFixed(ms % 1000 === 0 ? 0 : 1) + ' 秒';
     };

     const formatEstimateWait = (value, beyondClose = false) => {
       if (beyondClose) return '超过当日收盘';
       const ms = Number(value);
       if (!Number.isFinite(ms) || ms < 0) return '--';
       if (ms < 1000) return Math.round(ms) + ' 毫秒';
       const totalSeconds = Math.round(ms / 1000);
       if (totalSeconds < 60) return totalSeconds + ' 秒';
       const hours = Math.floor(totalSeconds / 3600);
       const minutes = Math.floor((totalSeconds % 3600) / 60);
       const seconds = totalSeconds % 60;
       if (hours > 0) return hours + ' 小时 ' + minutes + ' 分';
       return minutes + ' 分 ' + seconds + ' 秒';
     };

     const formatEstimatePrice = (value) => {
       const numeric = Number(value);
       if (!Number.isFinite(numeric)) return '--';
       return numeric.toFixed(isETF.value ? 3 : 2);
     };

     const closeExecutionEstimate = () => {
       executionEstimateVisible.value = false;
       executionEstimateRequestSeq += 1;
       executionEstimateLoading.value = false;
     };

     const openExecutionEstimate = async (order) => {
       const orderId = order?.order_local_id;
       if (!orderId || !selectSym.value || !selectDate.value || !selectTime.value) {
         createMessage.warning('当前订单或回放时间不完整，无法计算成交时间');
         return;
       }

       const seq = ++executionEstimateRequestSeq;
       executionEstimateOrder.value = order;
       executionEstimateData.value = null;
       executionEstimateError.value = '';
       executionEstimateLoading.value = true;
       executionEstimateVisible.value = true;

       try {
         const response = await getOrderExecutionEstimate({
           sym: selectSym.value,
           date: selectDate.value,
           time: selectTime.value,
           order_id: orderId,
         });
         if (seq !== executionEstimateRequestSeq) return;
         if (response.code === 0 && response.data) {
           executionEstimateData.value = response.data;
         } else {
           executionEstimateError.value = response.message || '订单成交时间分析失败';
         }
       } catch (error) {
         if (seq === executionEstimateRequestSeq) {
           executionEstimateError.value = '订单成交时间分析失败，请检查后端服务';
         }
       } finally {
         if (seq === executionEstimateRequestSeq) {
           executionEstimateLoading.value = false;
         }
       }
     };

     const toggleOrderLock = (order) => {
       const rawId = order && order.order_local_id;
       const id = rawId === undefined || rawId === null ? null : String(rawId);
       if (!id) return;

       const index = lockedOrderIds.value.findIndex((item) => String(item) === id);
       if (index >= 0) {
         lockedOrderIds.value.splice(index, 1);
         createMessage.success('已取消订单 ' + id + ' 的锁定');
       } else {
         lockedOrderIds.value.push(id);
         createMessage.success('已锁定订单 ' + id);
       }
     };

     // 清除锁定订单
     const unlockOrder = (orderId) => {
       const id = String(orderId);
       lockedOrderIds.value = lockedOrderIds.value.filter((item) => String(item) !== id);
       createMessage.success("已取消订单 " + id + " 的锁定");
     };

     const clearLockedOrders = () => {
       lockedOrderIds.value = [];
       createMessage.success('已清除所有锁定订单');
     };

     // 从GlobalOrderSearch跳转时的滚动到锁定订单位置（增强版）
     const scrollToLockedOrderFromGlobalSearch = (orderId) => {
       debugLog('scrollToLockedOrderFromGlobalSearch called with orderId:', orderId);
       
       if (!orderId) {
         console.warn('orderId is empty');
         return;
       }

       // 查找包含该订单的VolumeDataTable组件中的单元格
       let targetCell = null;
       let targetTableContainer = null;
       
       // 遍历所有可能的表格容器（ask1, ask2, ask3, bid1, bid2, bid3）
       const tableKeys = ['ask1', 'ask2', 'ask3', 'bid1', 'bid2', 'bid3'];
       
       for (const key of tableKeys) {
         const data = volumeData.value[key];
         if (data && data.data && data.data[0] && data.data[0][0]) {
           const rowData = data.data[0][0];
           
           // 查找包含该订单ID的单元格
           for (let i = 1; i <= 10000; i++) {
             const orderLocalIdKey = `v${i}_order_local_id`;
             if (
               rowData[orderLocalIdKey] !== undefined &&
               rowData[orderLocalIdKey] !== null &&
               String(rowData[orderLocalIdKey]).trim() === String(orderId).trim()
             ) {
               debugLog('Found order in', key, 'at column index:', i);
               
               // 找到对应的DOM元素
               // VolumeDataTable中的单元格通常有data-order-id属性或包含订单ID的内容
               const allCells = document.querySelectorAll('[data-order-id], .volume-cell');
               
               for (const cell of allCells) {
                 // 检查单元格是否包含该订单ID
                 if (cell.textContent && cell.textContent.includes(orderId)) {
                   targetCell = cell;
                   break;
                 }
               }
               
               if (targetCell) break;
             }
           }
           
           if (targetCell) break;
         }
       }
       
       // 如果没有找到单元格，尝试通过其他方式查找
       if (!targetCell) {
         debugLog('Searching for cell by orderId content');
         const allCells = document.querySelectorAll('.volume-cell, [data-order-id]');
         
         for (const cell of allCells) {
           if (cell.textContent && cell.textContent.trim() === orderId) {
             targetCell = cell;
             break;
           }
         }
       }
       
       if (!targetCell) {
         console.warn('Could not find cell for orderId:', orderId);
         createMessage.warning('未找到订单在表格中的位置');
         return;
       }
       
       debugLog('Found target cell, starting scroll process');
       
       // 找到表格容器（向上查找）
       let parent = targetCell.parentElement;
       for (let i = 0; i < 10 && parent; i++) {
         const style = window.getComputedStyle(parent);
         if (style.overflow === 'auto' || style.overflow === 'scroll' || 
             style.overflowY === 'auto' || style.overflowY === 'scroll') {
           targetTableContainer = parent;
           debugLog('Found scrollable table container at level:', i);
           break;
         }
         parent = parent.parentElement;
       }
       
       // 第一步：滚动表格容器
       if (targetTableContainer) {
         const cellRect = targetCell.getBoundingClientRect();
         const containerRect = targetTableContainer.getBoundingClientRect();
         
         // 计算表格内的滚动距离
         const scrollTop = cellRect.top - containerRect.top + targetTableContainer.scrollTop - (containerRect.height / 2);
         
         targetTableContainer.scrollTo({
           top: Math.max(0, scrollTop),
           behavior: 'smooth'
         });
         
         debugLog('Scrolling table container to position:', scrollTop);
       }
       
       // 第二步：滚动页面，确保表格容器在视野中
       setTimeout(() => {
         const cellRect = targetCell.getBoundingClientRect();
         
         // 如果单元格不在视野中，滚动页面
         if (cellRect.top < 0 || cellRect.top > window.innerHeight) {
           const scrollTop = window.pageYOffset + cellRect.top - (window.innerHeight / 2);
           
           window.scrollTo({
             top: Math.max(0, scrollTop),
             behavior: 'smooth'
           });
           
           debugLog('Scrolling page to position:', scrollTop);
         }
         
         // 第三步：添加闪烁效果突出显示
         targetCell.classList.add('highlight-flash');
         setTimeout(() => {
           targetCell.classList.remove('highlight-flash');
         }, 3000);
         
         createMessage.success('已定位到订单位置');
       }, 300);
     };

     // 滚动到锁定订单位置（通用版本，支持手动和自动导航）
     const scrollToLockedOrder = (orderId) => {
       const finalOrderId = orderId;
       
       debugLog('📍 [scrollToLockedOrder] 开始导航到订单');
       debugLog('   订单ID:', finalOrderId);
       
       if (!finalOrderId) {
         console.warn('❌ [scrollToLockedOrder] 订单ID为空');
         return;
       }
       
       // 第一步：在 volumeData 中查找订单的位置
       debugLog('🔍 [scrollToLockedOrder] 第一步：在 volumeData 中查找订单位置');
       let foundTable = null;
       let foundColumn = null;
       
       for (const key of ['ask1', 'ask2', 'ask3', 'bid1', 'bid2', 'bid3']) {
         const data = volumeData.value[key];
         if (data && data.data && data.data[0] && data.data[0][0]) {
           const rowData = data.data[0][0];
           // 遍历所有列（最多10000列）
           for (let i = 1; i <= 10000; i++) {
             const orderLocalIdKey = `v${i}_order_local_id`;
             if (
               rowData[orderLocalIdKey] !== undefined &&
               rowData[orderLocalIdKey] !== null &&
               String(rowData[orderLocalIdKey]).trim() === String(finalOrderId).trim()
             ) {
               foundTable = key;
               foundColumn = i;
               debugLog(`   ✓ 在 volumeData 中找到订单: 表格=${key}, 列=${i}`);
               break;
             }
           }
           if (foundTable) break;
         }
       }
       
       if (!foundTable || !foundColumn) {
         console.warn('❌ [scrollToLockedOrder] 在 volumeData 中未找到订单');
         debugLog('   - 搜索的订单ID:', finalOrderId);
         debugLog('   - 已锁定的订单ID列表:', lockedOrderIds.value);
         createMessage.warning('未找到订单在数据中的位置');
         return;
       }
       
       // 第二步：使用 CSS 选择器定位到具体的单元格
       debugLog('🔍 [scrollToLockedOrder] 第二步：在 DOM 中定位单元格');
       
       // 尝试多种选择器策略
       let targetCell = null;
       
       // 策略1: 通过表格容器和列索引查找
       // VolumeDataTable 组件可能有特定的类名或结构
       const tableSelectors = [
         `.volume-table-${foundTable}`,
         `[data-table="${foundTable}"]`,
         `.data-li:has(.volume-table) .volume-cell:nth-child(${foundColumn})`,
       ];
       
       for (const selector of tableSelectors) {
         try {
           const cells = document.querySelectorAll(selector);
           if (cells.length > 0) {
             targetCell = cells[0];
             debugLog(`   ✓ 通过选择器找到单元格: ${selector}`);
             break;
           }
         } catch (e) {
           // 选择器可能无效，继续尝试下一个
         }
       }
       
       // 策略2: 等待 Vue 渲染后查找高亮单元格（最可靠的方法）
       debugLog('   等待 Vue 渲染后查找高亮单元格...');
       
       // 定义滚动执行函数
       const performScrollToCell = (cell) => {
         debugLog('📜 [scrollToLockedOrder] 开始执行滚动');
         
         // 第二步：找到表格容器（向上查找）
         debugLog('🔍 查找表格容器');
         let tableContainer = null;
         let parent = cell.parentElement;
         
         for (let i = 0; i < 15 && parent; i++) {
           const style = window.getComputedStyle(parent);
           if (style.overflow === 'auto' || style.overflow === 'scroll' || 
               style.overflowY === 'auto' || style.overflowY === 'scroll') {
             tableContainer = parent;
             debugLog(`   ✓ 找到表格容器，层级: ${i}`);
             break;
           }
           parent = parent.parentElement;
         }
         
         if (!tableContainer) {
           debugLog('   ℹ️ 未找到表格容器，使用窗口滚动');
         }
         
         // 第三步：滚动表格容器
         if (tableContainer) {
           debugLog('📜 滚动表格容器');
           const cellRect = cell.getBoundingClientRect();
           const containerRect = tableContainer.getBoundingClientRect();
           
           const scrollLeft = cellRect.left - containerRect.left + tableContainer.scrollLeft - (containerRect.width / 2);
           const scrollTop = cellRect.top - containerRect.top + tableContainer.scrollTop - (containerRect.height / 2);
           
           debugLog(`   单元格位置: left=${cellRect.left}, top=${cellRect.top}`);
           debugLog(`   容器位置: left=${containerRect.left}, top=${containerRect.top}`);
           debugLog(`   计算滚动距离: left=${scrollLeft}, top=${scrollTop}`);
           
           tableContainer.scrollTo({
             left: Math.max(0, scrollLeft),
             top: Math.max(0, scrollTop),
             behavior: 'smooth'
           });
           
           debugLog('   ✓ 表格容器滚动完成');
         }
         
         // 第四步：滚动页面
         setTimeout(() => {
           debugLog('📜 滚动页面');
           const cellRect = cell.getBoundingClientRect();
           
           debugLog(`   单元格距离顶部: ${cellRect.top}`);
           debugLog(`   窗口高度: ${window.innerHeight}`);
           
           if (cellRect.top < 0 || cellRect.top > window.innerHeight) {
             const scrollTop = window.pageYOffset + cellRect.top - (window.innerHeight / 2);
             
             debugLog(`   计算页面滚动距离: ${scrollTop}`);
             
             window.scrollTo({
               top: Math.max(0, scrollTop),
               behavior: 'smooth'
             });
             
             debugLog('   ✓ 页面滚动完成');
           } else {
             debugLog('   ℹ️ 单元格已在视野中，无需滚动页面');
           }
           
           // 第五步：添加闪烁效果
           debugLog('✨ 添加闪烁效果');
           cell.classList.add('highlight-flash');
           debugLog('   ✓ 闪烁效果已添加，持续3秒');
           
           setTimeout(() => {
             cell.classList.remove('highlight-flash');
             debugLog('   ✓ 闪烁效果已移除');
           }, 3000);
           
           // 如果进度条正在显示，更新进度条信息；否则显示独立通知
           if (lockProgress.value.show) {
             lockProgress.value.detail = '已定位到订单位置，订单高亮显示中';
             updateLockProgressNotification();
           } else {
             createMessage.success('已定位到订单位置');
           }
           debugLog('✅ [scrollToLockedOrder] 导航完成');
         }, 300);
       };
       
       // 使用 nextTick 确保 Vue 已经更新 DOM
       nextTick(() => {
         const highlightedCells = document.querySelectorAll('.volume-cell.highlighted');
         debugLog(`   找到 ${highlightedCells.length} 个高亮单元格`);
         
         if (highlightedCells.length > 0) {
           // 找到包含我们订单的那个单元格
           // 由于我们刚刚锁定了这个订单，它应该是最后一个被高亮的
           targetCell = highlightedCells[highlightedCells.length - 1];
           debugLog('   ✓ 找到高亮单元格');
           
           // 执行滚动
           performScrollToCell(targetCell);
         } else {
           console.warn('   ❌ 未找到高亮单元格，可能 Vue 还未渲染');
           // 如果进度条正在显示，更新进度条信息；否则显示独立通知
           if (lockProgress.value.show) {
             lockProgress.value.detail = `找到订单位置（${foundTable} 列${foundColumn}），但单元格未高亮显示`;
             updateLockProgressNotification();
           } else {
             createMessage.warning(`找到订单位置（${foundTable} 列${foundColumn}），但单元格未高亮显示`);
           }
         }
       });
     };
     

    // 新版使用单一API获取订单统计信息
    const updateMarketData = async () => {
      if (!selectSym.value || !selectDate.value || !selectTime.value) {
        return;
      }

      try {
        const params = {
          sym: selectSym.value,
          date: selectDate.value,
          time: selectTime.value
        };

        const response = await getPastTimeTradeInfo(params);

        if (response.code === 0 && response.data) {
          // 只更新数据字段，保持固定的 level 列不变
          if (response.data.length > 0 && tradeData.value.length > 0) {
            // 根据 level 值匹配并更新对应的数据
            response.data.forEach((apiItem, index) => {
              if (index < tradeData.value.length) {
                // 保持原有的 level 值，只更新数据字段，空白值用 0 填充
                tradeData.value[index] = {
                  ...tradeData.value[index], // 保持原有的 level
                  last_1min: apiItem.last_1min || 0,
                  last_3s: apiItem.last_3s || 0,
                  last_500ms: apiItem.last_500ms || 0,
                  last_50ms: apiItem.last_50ms || 0,
                  last_10ms: apiItem.last_10ms || 0
                };
              }
            });
          }

          // 为了保持向后兼容，从第一个数据项提取marketData（1分钟数据）
          if (response.data.length > 0) {
            const firstItem = response.data[0]; // 买一新增挂单
            marketData.value = {
              bid_create_count: firstItem.last_1min || 0,
              bid_cancel_count: response.data[1]?.last_1min || 0, // 买一新增撤单
              bid_traded_count: response.data[2]?.last_1min || 0, // 买一新增成交
              ask_create_count: response.data[3]?.last_1min || 0, // 卖一新增挂单
              ask_cancel_count: response.data[4]?.last_1min || 0, // 卖一新增撤单
              ask_traded_count: response.data[5]?.last_1min || 0  // 卖一新增成交
            };
          }

        } else {
          createMessage.error(response.message || '获取订单统计信息失败');
        }
      } catch (error) {
        createMessage.error('网络请求失败，请检查网络连接');
      }
    };

    // 已删除getOrderIndexLevelInfo和getSelectTimeTradeInfo函数
    // 这些功能已合并到getOrderbookData中

    // WebSocket进度监听相关方法
    const startProgressListening = async (taskId) => {
      try {
        // 关闭之前的连接
        if (progressWebSocket) {
          progressWebSocket.close();
        }

        // 创建新的WebSocket连接
        progressWebSocket = createProgressListener(taskId);

        // 设置事件监听器
        progressWebSocket
          .onProgress((data) => {
            debugLog('🎯 Vue组件收到进度更新:', data);
            debugLog('📊 更新前的进度状态:', JSON.stringify(initProgress.value, null, 2));

            // 更新进度状态
            initProgress.value.progress = data.progress;
            initProgress.value.message = data.message;
            initProgress.value.status = data.status;

            // 更新进度条通知
            updateProgressNotification();

            debugLog('📈 更新后的进度状态:', JSON.stringify(initProgress.value, null, 2));
            debugLog('🎨 UI是否显示进度条:', initProgress.value.show);
          })
          .onComplete((data) => {
            debugLog('✅ Vue组件收到初始化完成:', data);
            initProgress.value.progress = 100;
            initProgress.value.message = '初始化完成，开始加载数据...';
            initProgress.value.status = 'completed';

            // 更新进度条通知为完成状态
            updateProgressNotification();

            debugLog('🎉 初始化完成，最终状态:', JSON.stringify(initProgress.value, null, 2));

            // 初始化完成后立即开始数据加载
            debugLog('🚀 初始化完成，立即开始数据加载');
            proceedWithDataLoading();

            createMessage.success('初始化完成，正在加载数据...');

            // 注意：WebSocket监听会在数据加载完成时自动停止
          })
          .onErrorOccurred((error) => {
            console.error('❌ Vue组件收到WebSocket错误:', error);
            initProgress.value.status = 'failed';
            initProgress.value.message = `错误: ${error}`;

            // 更新进度条通知为错误状态
            updateProgressNotification();

            // 根据错误类型显示不同的错误信息
            let errorMessage = `初始化失败: ${error}`;
            let isServerCrash = false;

            if (error.includes("场外基金") || error.includes("没有可用的盘口数据")) {
              errorMessage = error;
            } else if (error.includes("std::bad_alloc")) {
              errorMessage = "服务器内存不足: 当前数据集过大，服务器无法处理。建议：1）选择较小的数据集 2）联系管理员增加服务器内存";
              isServerCrash = true;
            } else if (error.includes("任务执行失败")) {
              errorMessage = "任务执行失败: 服务器在处理过程中出现错误";
              isServerCrash = true;
            } else if (error.includes("网络连接错误") || error.includes("服务器已断开") || error.includes("Connection refused")) {
              errorMessage = "服务器连接中断: 服务器可能在处理大型数据时崩溃。请检查服务器状态或稍后重试";
              isServerCrash = true;
            } else if (error.includes("连接失败：已达到最大重连次数") || error.includes("查询重试次数过多")) {
              errorMessage = "连接失败: 多次尝试连接服务器失败，服务器可能不可用";
              isServerCrash = true;
            } else if (error.includes("timeout") || error.includes("超时")) {
              errorMessage = "连接超时: 服务器响应超时，可能正在处理其他任务或负载过高";
            }

            // 如果是服务器崩溃，添加特殊提示
            if (isServerCrash) {
              errorMessage += '\n\n💡 建议操作：\n1. 检查服务器状态（点击"🩺检查服务器"按钮）\n2. 尝试处理较小的数据集\n3. 联系管理员查看服务器日志';
            }

            createMessage.error(errorMessage);

            // 失败后关闭进度条通知
            setTimeout(() => {
              closeProgressNotification();
              initProgress.value.show = false;
              debugLog('🔒 错误后进度条已隐藏');
            }, 8000); // 延长显示时间让用户看到完整错误信息
          })
          .onConnect(() => {
            debugLog('🔗 Vue组件：WebSocket连接成功');
            debugLog('🎯 当前任务信息:', {
              taskId: initProgress.value.taskId,
              symbol: selectSym.value,
              date: selectDate.value,
              wsUrl: `ws://10.1.228.20:18080/ws/progress/${initProgress.value.taskId}`
            });
          })
          .onDisconnect(() => {
            debugLog('🔌 Vue组件：WebSocket连接断开');
          });

        // 开始连接
        await progressWebSocket.connect();

        debugLog('🚀 WebSocket连接已建立，taskId:', taskId);

      } catch (error) {
        console.error('❌ 启动WebSocket进度监听失败:', error);
        createMessage.error('无法连接进度服务，请检查网络连接');
        initProgress.value.show = false;
      }
    };





    // 获取进度条颜色
    const getProgressColor = (status) => {
      switch (status) {
        case 'initializing':
          return '#1890ff'; // 蓝色
        case 'completed':
          return '#52c41a'; // 绿色
        case 'failed':
          return '#ff4d4f'; // 红色
        default:
          return '#1890ff';
      }
    };

    // 组件销毁时清理WebSocket连接和进度条通知
    onUnmounted(() => {
      if (progressWebSocket) {
        progressWebSocket.close();
        progressWebSocket = null;
      }
      // 关闭进度条通知
      closeProgressNotification();
      
      // 清空 localStorage 中的选择数据（关闭标签页时清空）
      debugLog('🧹 [VolumeQueue] 组件卸载，清空 localStorage 数据');
      localStorage.removeItem('volumeQueue_selectSym');
      localStorage.removeItem('volumeQueue_selectDate');
      localStorage.removeItem('volumeQueue_timestamp');
      localStorage.removeItem('volumeQueue_orderIndex');
      debugLog('   ✓ 已清空股票代码、日期、时间戳和订单索引');
    });
    
    // 处理从GlobalOrderSearch跳转到订单快照并锁定订单
    const handleGoToOrderSnapshotAndLock = (orderId) => {
      // 检查是否已选择股票代码和日期
      if (!selectSym.value || !selectDate.value) {
        createMessage.warning('请先选择股票代码和日期，然后再尝试跳转到订单快照');
        return;
      }
      
      // 使用订单ID查询快照
      const params = {
        sym: selectSym.value,
        date: selectDate.value,
        id: parseInt(orderId)
      };
      
      loading.value = true;
      createMessage.loading('正在查找订单快照...', 0);
      
      getSnapshotById(params).then((res) => {
        createMessage.destroy(); // 关闭loading消息
        
        if (res.code === 0 && res.data && res.data.snapshot) {
          const snapshot = res.data.snapshot;
          
          // 更新快照数据
          updateVolumeDataFromSnapshot(snapshot, res.data);
          
          // 更新市场数据
          updateMarketData();
          
          // 锁定订单
          lockOrderById(orderId);
          
          createMessage.success(`已跳转到订单 ${orderId} 的快照并锁定该订单`);
        } else {
          createMessage.error(res.message || `未找到订单 ${orderId} 的快照`);
        }
      }).catch((error) => {
        createMessage.destroy(); // 关闭loading消息
        createMessage.error('查询订单快照失败，请检查网络连接');
        console.error('查询订单快照失败:', error);
      }).finally(() => {
        loading.value = false;
      });
    };
    
    // 统一的订单锁定处理器 - 整合所有锁定相关逻辑（带进度条）
    const unifiedOrderLockHandler = async (lockData) => {
      // 提取并格式化时间（在函数开头声明，避免重复声明）
      let timeOnly = lockData.timestamp;
      if (timeOnly.includes(' ')) {
        timeOnly = timeOnly.split(' ')[1];
      }
      if (timeOnly.includes('.')) {
        const [timePart, msPart] = timeOnly.split('.');
        timeOnly = `${timePart}.${msPart.substring(0, 3)}`;
      }
      
      // 初始化进度状态
      lockProgress.value = {
        show: true,
        message: '准备锁定订单...',
        detail: `订单ID: ${lockData.orderId} | ${lockData.sym} | ${lockData.date}`,
        progress: 0,
        status: 'processing',
        currentStep: 0,
        totalSteps: 7
      };
      
      // 创建进度条通知
      createLockProgressNotification();
      
      try {
        // 步骤1: 设置股票代码和日期 (14%)
        lockProgress.value.currentStep = 1;
        lockProgress.value.progress = 14;
        lockProgress.value.message = '设置股票代码和日期...';
        lockProgress.value.detail = `股票代码: ${lockData.sym} | 交易日期: ${lockData.date}`;
        updateLockProgressNotification();
        
        selectSym.value = lockData.sym;
        securityType.value = getSecurityType(lockData.sym);
        selectDate.value = lockData.date;
        hasSymbol.value = true;
        hasDate.value = true;
        localStorage.setItem('volumeQueue_selectSym', lockData.sym);
        localStorage.setItem('volumeQueue_securityType', securityType.value);
        localStorage.setItem('volumeQueue_selectDate', lockData.date);
        
        await new Promise(resolve => setTimeout(resolve, 300));
        
        // 步骤2: 设置时间 (28%)
        lockProgress.value.currentStep = 2;
        lockProgress.value.progress = 28;
        lockProgress.value.message = '设置跳转时间...';
        updateLockProgressNotification();
        
        selectTime.value = timeOnly;
        localStorage.setItem('volumeQueue_timestamp', timeOnly);
        lockProgress.value.detail = `跳转时间: ${timeOnly}`;
        
        await new Promise(resolve => setTimeout(resolve, 300));
        
        // 步骤3: 填充订单ID (42%)
        lockProgress.value.currentStep = 3;
        lockProgress.value.progress = 42;
        lockProgress.value.message = '填充订单ID...';
        lockProgress.value.detail = `订单ID: ${lockData.orderId}`;
        updateLockProgressNotification();
        
        lockByIdValue.value = lockData.orderId;
        
        await new Promise(resolve => setTimeout(resolve, 300));
        
        // 步骤4: 加载日期列表 (56%)
        lockProgress.value.currentStep = 4;
        lockProgress.value.progress = 56;
        lockProgress.value.message = '加载日期列表...';
        updateLockProgressNotification();
        
        if (!datesData.value.length) {
          loading.value = true;
          const params = { sym: lockData.sym };
          
          try {
            const res = await getDateList(params);
            loading.value = false;
            
            if (res.code == 0) {
              datesData.value = res.data.map(item => ({ label: item, value: item }));
              lockProgress.value.detail = `已加载 ${datesData.value.length} 个交易日期`;
              updateLockProgressNotification();
            }
          } catch (error) {
            loading.value = false;
            lockProgress.value.detail = '加载日期列表失败';
            updateLockProgressNotification();
            console.error('加载日期列表失败:', error);
          }
        } else {
          lockProgress.value.detail = `日期列表已存在 (${datesData.value.length} 个)`;
          updateLockProgressNotification();
        }
        
        // 步骤5: 加载订单数据 (70%)
        lockProgress.value.currentStep = 5;
        lockProgress.value.progress = 70;
        lockProgress.value.message = '加载订单数据...';
        lockProgress.value.detail = '正在初始化 TradeBook...';
        updateLockProgressNotification();
        
        if (hasSymbol.value && hasDate.value && !loading.value) {
          await getOrderbookData();
          
          // 重要：getOrderbookData 会重置 selectTime.value 为 09:30:00.000
          // 需要重新设置为用户输入的时间（使用函数开头提取的 timeOnly）
          selectTime.value = timeOnly;
          debugLog('   ✓ [unifiedOrderLockHandler] 重新设置跳转时间为:', selectTime.value);
          
          lockProgress.value.detail = '订单数据加载完成';
          updateLockProgressNotification();
        }
        
        // 步骤6: 跳转到指定时间 (84%)
        lockProgress.value.currentStep = 6;
        lockProgress.value.progress = 84;
        lockProgress.value.message = '跳转到指定时间...';
        lockProgress.value.detail = `目标时间: ${timeOnly}`;
        updateLockProgressNotification();
        
        await new Promise(resolve => setTimeout(resolve, 2000));
        
        // 调用前再次确认时间
        debugLog('   ✓ [步骤6] 调用 clickMoveTime，传入时间参数:', timeOnly);
        await clickMoveTime(timeOnly); // 直接传入时间参数，不依赖 selectTime.value

        // 创建事件时间可能早于订单进入盘口的第一条快照，沿变化点向前补齐。
        if (!isOrderVisibleInCurrentSnapshot(lockData.orderId)) {
          lockProgress.value.detail = '创建时间快照尚未包含订单 ' + lockData.orderId + '，正在查找其进入盘口的时刻...';
          updateLockProgressNotification();
          await findFirstSnapshotContainingOrder(lockData.orderId, timeOnly);
        }

        lockProgress.value.detail = '时间跳转完成';
        updateLockProgressNotification();
        
        // 步骤7: 锁定订单并滚动 (100%)
        lockProgress.value.currentStep = 7;
        lockProgress.value.progress = 98;
        lockProgress.value.message = '锁定订单并定位...';
        lockProgress.value.detail = `正在查找订单 ${lockData.orderId}...`;
        updateLockProgressNotification();
        
        await new Promise(resolve => setTimeout(resolve, 1000));
        
        const lockSuccess = lockOrdersById(true);
        
        if (lockSuccess) {
          // 成功完成
          lockProgress.value.progress = 100;
          lockProgress.value.status = 'completed';
          lockProgress.value.message = '订单锁定成功！';
          lockProgress.value.detail = `订单 ${lockData.orderId} 已锁定并定位到页面`;
          updateLockProgressNotification();
          
          // 延迟关闭进度条
          setTimeout(() => {
            closeLockProgressNotification();
            lockProgress.value.show = false;
          }, 2500);
          
        } else {
          // 锁定失败
          lockProgress.value.status = 'failed';
          lockProgress.value.message = '订单锁定失败';
          lockProgress.value.detail = `未找到订单 ${lockData.orderId}，请检查数据是否已加载`;
          updateLockProgressNotification();
          
          // 延迟关闭进度条
          setTimeout(() => {
            closeLockProgressNotification();
            lockProgress.value.show = false;
          }, 4000);
        }
        
      } catch (error) {
        console.error('订单锁定流程失败:', error);
        
        // 失败状态
        lockProgress.value.status = 'failed';
        lockProgress.value.message = '订单锁定流程失败';
        lockProgress.value.detail = `错误: ${error.message || '未知错误'}`;
        updateLockProgressNotification();
        
        // 延迟关闭进度条
        setTimeout(() => {
          closeLockProgressNotification();
          lockProgress.value.show = false;
        }, 4000);
      }
    };

    // 跳转到指定时间并锁定订单
    const jumpToTimeWithLock = async (timestamp, orderId) => {
      debugLog('开始跳转到时间:', timestamp, '订单ID:', orderId);
            // 边界检测
        const checkResult = checkTimeBoundary(timestamp);
        selectTime.value = checkResult.corrected;
        localStorage.setItem('volumeQueue_timestamp', checkResult.corrected);
        debugLog('   ✓ 时间已设置并保存:', checkResult.corrected);

      // 延迟执行，确保数据加载完成
      return new Promise((resolve, reject) => {
        setTimeout(async () => {
          try {
            // 加载该时间点的数据
            await clickMoveTime();
            
            // 等待一段时间确保DOM更新
            setTimeout(() => {
              // 锁定订单
              const lockResult = lockOrderById(orderId);
              
              if (lockResult) {
                // 滚动到锁定订单位置
                scrollToLockedOrder(orderId);
                
                // 清除localStorage中的锁定标记
                clearLockDataFromStorage();
                
                const formattedTime = formatTimestamp(timestamp);
                createMessage.success(`订单 ${orderId} 锁定成功 (${formattedTime})`);
                resolve(true);
              } else {
                const error = new Error(`未找到匹配的订单ID: ${orderId}`);
                const result = handleOrderLockError(error);
                showOrderLockFeedback(result);
                reject(error);
              }
            }, 1000); // 等待1秒确保DOM更新完成
            
          } catch (error) {
            console.error('跳转时间并锁定订单失败:', error);
            const result = handleOrderLockError(error);
            showOrderLockFeedback(result);
            reject(error);
          }
        }, 500); // 延迟500ms执行
      });
    };

    // 清除localStorage中的锁定数据
    const clearLockDataFromStorage = () => {
      const lockKeys = [
        'volumeQueue_lockOrderId',
        'volumeQueue_lockSym', 
        'volumeQueue_lockDate',
        'volumeQueue_lockTimestamp',
        'volumeQueue_isFromGlobalSearch'
      ];
      
      lockKeys.forEach(key => {
        localStorage.removeItem(key);
      });
      
      debugLog('已清除localStorage中的锁定数据');
    };

    // 根据订单ID锁定订单（返回布尔值表示是否成功）
    const lockOrderById = (orderId) => {
      let found = false;

      // 遍历所有volumeData中的订单数据
      outerLoop: for (const key of Object.keys(volumeData.value)) {
        if (key.includes('ask') || key.includes('bid')) {
          const data = volumeData.value[key];
          if (data && data.data && data.data[0] && data.data[0][0]) {
            const rowData = data.data[0][0];
            // 遍历所有v1, v2, v3...列
             for (let i = 1; i <= 10000; i++) {
               const orderLocalIdKey = `v${i}_order_local_id`;
               if (
                 rowData[orderLocalIdKey] !== undefined &&
                 rowData[orderLocalIdKey] !== null &&
                 String(rowData[orderLocalIdKey]).trim() === String(orderId).trim()
               ) {
                 // 直接锁定匹配的order_local_id
                 if (!lockedOrderIds.value.includes(orderId)) {
                   lockedOrderIds.value.push(orderId);
                   debugLog('成功锁定订单ID:', orderId);
                 }
                 found = true;
                 break outerLoop; // 找到后立即退出所有遍历
               }
             }
          }
        }
      }

      if (!found) {
        debugLog('未找到匹配的订单ID:', orderId);
        createMessage.info(`未找到匹配的订单ID: ${orderId}`);
      }
      
      return found;
    };
</script>
<style lang="less" scoped>
  // ==================== 第 4 步：新布局样式 ====================
  .ov-page {
    display: flex;
    flex-direction: column;
    height: calc(100vh - 24px);
    margin: 12px;
    padding-bottom: 8px;
    overflow: hidden;
    background: #fff;

    // D2 等宽数字：页面内所有 .num 数字
    .num {
      font-variant-numeric: tabular-nums;
      font-feature-settings: 'tnum';
    }
  }

  // ① 顶栏（两行）
  .ov-topbar {
    border: 1px solid #e8eef5;
    border-radius: 6px;
    background: #fbfcfe;
    padding: 6px 10px;
    margin-bottom: 8px;
  }

  .ov-topbar-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px 12px;
    min-height: 36px;

    // 控制条（时间/订单/跳转/锁定）不换行：窄屏横向滚动，避免折行占用纵向空间
    &.controls {
      flex-wrap: nowrap;
      overflow-x: auto;
      border-top: 1px dashed #edf1f7;
      padding-top: 6px;
      margin-top: 6px;

      .ov-bar-group {
        flex: 0 0 auto;
      }
    }
  }

  .ov-bar-group {
    display: flex;
    align-items: center;
    gap: 6px;

    .group-label {
      color: #425466;
      font-size: 12px;
      font-weight: 600;
      white-space: nowrap;
    }
  }

  .step-label {
    color: #667085;
    font-size: 11px;
    font-weight: 600;
    white-space: nowrap;
  }

  .ov-select {
    &.w-type { width: 90px; }
    &.w-sym { width: 130px; }
    &.w-date { width: 140px; }
    &.w-step { width: 104px; }
  }

  .ov-time-input { width: 104px; }
  .ov-lock-input { width: 68px; &.w-id { width: 96px; } }

  .ov-bar-status {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 18px;
    margin-left: auto;
    color: #667085;
    font-size: 12px;

    .status-item b {
      color: #1d2939;
      font-weight: 600;
      margin-left: 2px;
    }

    .status-item.time b {
      color: #096dd9;
    }

    .theme-toggle {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    .ov-user-dropdown {
      display: inline-flex;
      align-items: center;

      // 迁入顶栏后去掉 header 固定高度，与状态文字对齐
      :deep(.vben-header-user-dropdown) {
        height: auto;
        padding: 0 4px;

        img {
          margin-right: 6px;
        }
      }
    }
  }

  // 迁入顶栏的订单查询按钮：压缩为与顶栏一致的控件高度
  .ov-global-search {
    display: inline-flex;
    align-items: center;

    :deep(.search-trigger-button) {
      min-height: 32px;
      height: 32px;
      padding: 0 10px;
      font-size: 12px;

      &:hover {
        transform: none;
        box-shadow: none;
      }
    }
  }

  .locked-orders-popover {
    width: 300px;
  }

  .locked-orders-popover-header,
  .locked-order-item-main {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .locked-orders-popover-header {
    padding-bottom: 6px;
    border-bottom: 1px solid #edf1f7;
    color: #425466;
    font-size: 12px;
    font-weight: 600;
  }

  .locked-order-list {
    max-height: 280px;
    overflow-y: auto;
  }

  .locked-order-item {
    padding: 7px 0;
    border-bottom: 1px solid #f0f2f5;
  }

  .locked-order-item:last-child {
    border-bottom: 0;
  }

  .locked-order-id {
    color: #1d2939;
    font-size: 12px;
    font-weight: 600;
  }

  .locked-order-item-meta,
  .locked-orders-empty {
    color: #98a2b3;
    font-size: 11px;
  }

  .locked-order-item-meta {
    margin-top: 2px;
  }

  .locked-orders-empty {
    padding: 12px 0 4px;
    text-align: center;
  }

  .locked-status {
    cursor: pointer;
  }

  // 主体左右分栏：左数据区 55% / 右图表区 45%
  // 两栏严格等高（= 视口剩余高度），内容超出时栏内滚动，保证一屏布局不顶天立地
  .ov-body {
    display: flex;
    flex: 1;
    gap: 8px;
    align-items: stretch;
    min-height: 0;
  }

  .ov-data-area {
    display: flex;
    flex: 0 0 55%;
    flex-direction: column;
    min-width: 0;
    height: 100%;
    overflow-y: auto;
  }

  .ov-chart-area {
    flex: 1;
    min-width: 0;
    height: 100%;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 8px;

    // 统计表/Q-t 图固定置顶，不被锁定订单图挤出首屏；Q-t 图保持固定紧凑高度
    // 锁定订单图也按自然高度排列，内容超出时整栏滚动（不做卡片内部滚动，
    // 避免 flex 压缩把摘要表格等子块挤成 0 高度）
    > .chart-card {
      flex: 0 0 auto;
    }
  }

  .chart-card {
    border: 1px solid #e8eef5;
    border-radius: 6px;
    background: #fbfcfe;
    padding: 8px 10px;
  }

  .chart-card-title {
    color: #425466;
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 6px;
  }

  .chart-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 120px;
    color: #98a2b3;
    font-size: 12px;
    border: 1px dashed #d9e2ec;
    border-radius: 4px;
  }

  // ② 队列区：买左卖右，档内 3 在左/右外侧、1 靠中间；纵向撑满左栏
  .ov-queue-zones {
    display: flex;
    flex: 1;
    gap: 8px;
    min-height: 0;
  }

  .ov-queue-zones.expanded {
    flex-direction: column;
  }

  .ov-queue-zones.expanded .ov-zone {
    width: 100%;
  }

  .ov-zone {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
  }

  // 窄屏：图表区收到底部，解除等高约束恢复自然流式高度
  @media (max-width: 1440px) {
    .ov-page {
      height: auto;
    }

    .ov-body {
      flex-direction: column;
    }

    .ov-data-area,
    .ov-chart-area {
      flex: 1 1 auto;
      width: 100%;
      height: auto;
      overflow-y: visible;
    }
  }

  // D1 暗色主题（可选）
  .ov-page.ov-dark {
    background: #14181f;

    .ov-topbar,
    .chart-card {
      border-color: #2c3342;
      background: #1b212c;
    }

    .ov-topbar-row.controls {
      border-top-color: #2c3342;
    }

    .chart-card-title,
    .group-label {
      color: #aab4c5;
    }

    .chart-placeholder {
      border-color: #2c3342;
      color: #5d6778;
    }

    .ov-bar-status {
      color: #8b95a7;

      .status-item b { color: #e2e8f2; }
      .status-item.time b { color: #69b1ff; }
    }
  }

  .filter-mode {
    padding: 4px 8px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    min-height: 34px;

    &.compact-row {
      padding: 3px 8px;
      margin-bottom: 3px;
    }

    .filter-item {
      margin-top: 0px;
      margin-right: 8px;
      margin-bottom: 0px;
      display: flex;
      align-items: center;
      min-width: 0;

      &.info-item {
        margin-right: 15px;
      }

      &.locked-orders-info {
        background-color: #f0f8ff;
        padding: 2px 8px;
        border-radius: 4px;
        border: 1px solid #d1ecf1;
      }

      &.lock-controls {
        display: flex;
        align-items: center;
        gap: 0;
        flex-wrap: wrap;
        row-gap: 6px;

        .field-label {
          margin-right: 8px;
          white-space: nowrap;
        }
      }

      .item-name {
        padding: 0 8px;
      }
    }

    .field-label {
      margin-right: 5px;
      font-size: 13px;
      white-space: nowrap;
    }

    .info-label {
      font-size: 12px;
      color: #666;
      white-space: nowrap;
    }

    .section-label {
      margin-right: 8px;
      font-size: 13px;
      font-weight: 500;
      white-space: nowrap;
      min-width: 68px;
    }

    .button-group {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }

    .time-btn,
    .order-btn {
      width: 78px;
      height: 27px;
      font-size: 12px;
      padding: 0 5px;
    }
  }

  .primary-filter-row {
    display: grid;
    grid-template-columns: minmax(150px, 1fr) minmax(180px, 1.35fr) minmax(180px, 1.2fr) auto;
    gap: 8px;
    padding-top: 2px;
    padding-bottom: 8px;
    border-bottom: 1px solid #e1e6ed;

    .filter-item {
      margin-right: 0;
      min-width: 0;
    }

    :deep(.ant-select) {
      width: 100% !important;
      margin-left: 0 !important;
    }

    :deep(.ant-btn) {
      min-width: 64px;
    }
  }

  .navigation-row,
  .lock-row {
    padding-top: 8px;
    padding-bottom: 4px;
  }

  .navigation-row {
    justify-content: space-between;

    .filter-item {
      margin-right: 0;
    }
  }

  .lock-row {
    border-bottom: 1px solid #e1e6ed;
    padding-bottom: 8px;

    .lock-controls {
      width: 100%;
      margin-right: 0;
    }

    :deep(.ant-input) {
      max-width: 150px;
    }
  }

  .movement-row {
    padding-top: 4px;
    padding-bottom: 2px;

    .button-group {
      flex: 1;
    }
  }

  .echart-ul {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    width: 100%;
    .echart-li {
      width: 530px;
      flex-shrink: 1;
    }
  }
  .main-mode {
    .data-ul {
      display: flex;
      justify-content: flex-start;
      width: 100%;
      align-items: flex-start;
      .data-li {
        flex: 1;
        margin-top: 5px;
        margin-right: 5px;
        background: #e9e7e7;
      }
    }
    .data-ul-top {
      display: grid;
      grid-template-columns: minmax(620px, 3fr) minmax(280px, 2fr) minmax(360px, 4fr);
      gap: 8px;
      width: 100%;
      min-height: 280px;
      height: auto;
      align-items: stretch;
      .data-li:nth-child(1) {
        min-width: 0;
        margin: 0;
        padding: 10px 12px;
        background: #f5f7fa;
        border: 1px solid #e1e6ed;
        border-radius: 6px;
        box-sizing: border-box;
      }

      .data-li:nth-child(2) {
        min-width: 0;
        margin: 0;
        background: #f5f7fa;
        border: 1px solid #e1e6ed;
        border-radius: 6px;
        overflow: hidden;
      }

      .data-li:nth-child(3) {
        min-width: 0;
        margin: 0;
        background: #f5f7fa;
        border: 1px solid #e1e6ed;
        border-radius: 6px;
        overflow: hidden;
      }
    }

    .empty-mode {
      height: calc(100vh - 300px);
      display: flex;
      align-items: center;
      justify-content: center;
    }
  }

  @media (max-width: 1280px) {
    .main-mode .data-ul-top {
      grid-template-columns: minmax(560px, 3fr) minmax(260px, 2fr);

      .data-li:nth-child(3) {
        grid-column: 1 / -1;
      }
    }
  }

  @media (max-width: 900px) {
    .main-mode .data-ul-top {
      grid-template-columns: minmax(0, 1fr);

      .data-li:nth-child(3) {
        grid-column: auto;
      }
    }
  }

  @media (max-width: 760px) {
    .primary-filter-row {
      grid-template-columns: 1fr 1fr;
    }

    .primary-filter-row .filter-item:last-child {
      justify-content: flex-start;
    }
  }

  .dropdown-list {
    max-height: 200px; /* 设置一个合适的最大高度 */
    overflow-y: auto; /* 当内容超过最大高度时显示滚动条 */
    position: absolute; /* 设置绝对定位 */
    z-index: 1000; /* 确保下拉列表在其他元素之上 */
    background-color: white;
  }
  :deep(.ant-table-thead) {
    .header-transaction-amount {
      background-color: #e9e7e7;
    }
  }

  // 第二个表格的表头高度减少
  .data-li:nth-child(2) {
    :deep(.ant-table-thead > tr > th) {
      height: 75px !important;
      padding: 8px 8px !important;
      font-size: 12px;
      line-height: 1.2;
    }
  }

  // 第三个表格的表头高度减少
  .data-li:nth-child(3) {
    :deep(.ant-table-thead > tr > th) {
      height: 75px !important;
      padding: 4px 8px !important;
      font-size: 12px;
      line-height: 1.2;
    }
  }
  :deep(.ant-table-tbody > tr) {
    height: 30px; // 设置所有行的固定高度
    font-size: 13px; // 确保字体大小一致
    td {
      padding: 0px; // 确保内边距一致
      vertical-align: middle; // 设置垂直对齐方式
      text-align: center; // 设置水平对齐方式
    }
  }
  
  // 高亮闪烁效果
  :deep(.volume-cell.highlight-flash) {
    animation: highlightFlash 2s ease-in-out;
  }
  
  @keyframes highlightFlash {
    0% {
      background-color: rgba(24, 144, 255, 0.2);
      box-shadow: 0 0 8px rgba(24, 144, 255, 0.8);
    }
    50% {
      background-color: rgba(24, 144, 255, 0.6);
      box-shadow: 0 0 12px rgba(24, 144, 255, 1);
    }
    100% {
      background-color: rgba(24, 144, 255, 0.2);
      box-shadow: 0 0 8px rgba(24, 144, 255, 0.8);
    }
  }
  .execution-estimate-body {
    min-height: 180px;
    color: #344054;
  }

  .execution-estimate-body :deep(.ant-spin-nested-loading),
  .execution-estimate-body :deep(.ant-spin-container) {
    min-height: 180px;
  }

  .estimate-error,
  .estimate-unavailable,
  .estimate-empty {
    padding: 28px 16px;
    border: 1px dashed #d0d5dd;
    border-radius: 6px;
    color: #667085;
    text-align: center;
  }

  .estimate-error {
    border-color: #ffccc7;
    color: #cf1322;
    background: #fff2f0;
  }

  .estimate-order-summary {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 8px;
    margin-bottom: 12px;
  }

  .estimate-order-summary > div,
  .estimate-detail-grid > div {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
    padding: 8px 10px;
    border-radius: 5px;
    background: #f7f9fc;
  }

  .estimate-order-summary span,
  .estimate-detail-grid span {
    color: #667085;
    font-size: 11px;
  }

  .estimate-order-summary b,
  .estimate-detail-grid b {
    overflow: hidden;
    color: #1d2939;
    font-size: 13px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .estimate-order-summary .bid-text {
    color: #f5222d;
  }

  .estimate-order-summary .ask-text {
    color: #52c41a;
  }

  .estimate-section {
    margin-top: 12px;
    padding: 12px;
    border: 1px solid #e4e7ec;
    border-radius: 7px;
  }

  .prediction-section {
    border-color: #b7d7ff;
    background: #f8fbff;
  }

  .actual-section {
    background: #fcfcfd;
  }

  .estimate-section-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    color: #1d2939;
    font-size: 14px;
    font-weight: 600;
  }

  .confidence-badge {
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
    cursor: help;
  }

  .estimate-windows {
    margin-top: 10px;
  }

  .estimate-windows-title {
    margin-bottom: 5px;
    color: #667085;
    font-size: 11px;
  }

  .estimate-window-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
    font-variant-numeric: tabular-nums;

    th,
    td {
      padding: 4px 8px;
      border-bottom: 1px solid #e6eef8;
      text-align: center;
      white-space: nowrap;
    }

    th {
      color: #667085;
      font-weight: 600;
      background: #fff;
    }

    td {
      color: #1d2939;
    }

    .row-selected td {
      background: #e6f4ff;
      font-weight: 600;
    }

    .cell-unavailable {
      color: #b54708;
      cursor: help;
      text-decoration: underline dotted;
    }
  }

  .estimate-warnings {
    margin: 8px 0 0;
    padding-left: 20px;
    color: #b54708;
    font-size: 11px;
  }

  .estimate-footnote {
    margin-top: 10px;
    padding: 6px 12px 2px;
    border-left: 3px solid #d1e9ff;
    color: #667085;
    font-size: 11px;
    line-height: 1.5;
  }

  .confidence-high {
    color: #027a48;
    background: #d1fadf;
  }

  .confidence-medium {
    color: #b54708;
    background: #fef0c7;
  }

  .confidence-low {
    color: #b42318;
    background: #fee4e2;
  }

  // 分窗口表格的可信度得分配色与徽标一致
  .window-score {
    font-weight: 600;

    &.score-high { color: #027a48; }
    &.score-medium { color: #b54708; }
    &.score-low { color: #b42318; }
  }

  .estimate-time-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    margin-bottom: 10px;
  }

  .estimate-time-card {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 12px;
    border: 1px solid #d1e9ff;
    border-radius: 6px;
    background: #fff;
  }

  .estimate-time-card span {
    color: #667085;
    font-size: 11px;
  }

  .estimate-time-card strong {
    color: #175cd3;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 20px;
  }

  .estimate-time-card small {
    color: #475467;
  }

  .estimate-detail-grid {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 7px;
    margin-bottom: 4px;
  }

  @media (max-width: 960px) {
    .estimate-order-summary,
    .estimate-detail-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .estimate-time-grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }

  // 弹窗留白：不顶天立地，四周留空隙
  :deep(.execution-estimate-modal .ant-modal) {
    max-width: calc(100vw - 64px);
  }

</style>

<!-- 悬浮细分表渲染在 body（teleport），scoped 覆盖不到，单独全局样式 -->
<style lang="less">
  .score-tip-overlay {
    .ant-tooltip-inner {
      width: max-content;
      max-width: calc(100vw - 32px);
      box-sizing: border-box;
      padding: 8px 10px;
      overflow: hidden;
    }

    .score-tip-table {
      width: auto;
      border-collapse: collapse;
      font-size: 12px;
      font-variant-numeric: tabular-nums;

      td {
        padding: 2px 6px;
        white-space: nowrap;
      }

      .score-tip-name {
        color: rgba(255, 255, 255, 0.75);
      }

      .score-tip-value {
        color: #fff;
        font-weight: 600;
        text-align: right;
      }
    }
  }

  .confidence-tip-overlay {
    .ant-tooltip-inner {
      min-width: 260px;
      max-width: min(560px, calc(100vw - 32px));
      padding: 9px 12px;
      box-sizing: border-box;
    }

    .confidence-reasons {
      line-height: 1.7;
      white-space: nowrap;
    }

    .confidence-reason-note {
      margin-top: 4px;
      padding-top: 4px;
      border-top: 1px solid rgba(255, 255, 255, 0.2);
      white-space: normal;
    }
  }
</style>
