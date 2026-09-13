(function initRatingPresentation(globalObject) {
  "use strict";

  const STATUS_LABELS = {
    valid: "估值有效",
    insufficient_target_games: "样本不足",
    above_reference_range: "高于估值范围上限",
    below_reference_range: "低于估值范围下限",
    multiple_minima: "存在多个接近的估值点",
    multiple_crossings: "估值曲线存在多个交点",
    low_resolution: "估值精度不足",
    insufficient_reference: "参考数据不足",
  };

  const STATUS_REASON_LABELS = {
    fewer_than_minimum_complete_recent_target_games: "完整的近期有效对局少于最低要求",
    fewer_than_10_complete_recent_target_games: "完整的近期有效对局少于 10 局",
    J_continues_improving_to_upper_boundary: "目标函数到估值上限仍在改善",
    J_continues_improving_to_lower_boundary: "目标函数到估值下限仍在改善",
    nll_continues_improving_to_upper_boundary: "拟合结果到估值上限仍在改善",
    nll_continues_improving_to_lower_boundary: "拟合结果到估值下限仍在改善",
    candidateZ_is_negative_over_the_full_grid: "完整估值范围内的曲线均指向更高 Rating",
    candidateZ_is_positive_over_the_full_grid: "完整估值范围内的曲线均指向更低 Rating",
    separated_near_minimum_regions: "存在彼此分离、但拟合程度接近的最低区域",
    all_discovered_basins_retained: "所有发现的接近最低区域均被保留",
    multiple_separated_zero_crossings: "存在多个彼此分离的零交点",
    calibrated_near_tied_minima: "校准后仍存在多个接近的最低点",
    calibrated_score_set_has_multiple_intervals: "95% 校准结果包含多个不连续区间",
    calibrated_allowed_set_is_discontinuous: "95% 校准结果包含多个不连续区间",
    calibrated_minimum_region_is_wide: "最低区域过宽，无法给出精确估值",
    calibrated_local_curve_jump: "估值曲线局部跳变，无法给出稳定估值",
    no_grid_point_was_scorable: "估值范围内没有可评分的参考点",
    no_complete_grid_point: "估值范围内没有完整的参考点",
    no_complete_J_value: "估值范围内没有完整的目标函数值",
    beta_binomial_fit_failed: "校准模型拟合失败",
    validated_v3_T95_is_not_available: "95% 校准阈值不可用",
    validated_v4_T95_is_not_available: "95% 校准阈值不可用",
    independent_validation_did_not_confirm_database_95_percent_coverage: "独立验证未确认该 95% 区间的覆盖率",
    independent_validation_did_not_confirm_95_percent_coverage: "独立验证未确认该 95% 区间的覆盖率",
  };

  const EXCLUSION_LABELS = {
    opponent_out_of_reference_range: "对手 Rating 不在参考范围",
    incomplete_phase_data: "阶段数据不完整",
  };

  function finiteNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function integer(value) {
    const number = finiteNumber(value);
    return number === null ? "—" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number);
  }

  function intervalText(interval) {
    const lower = finiteNumber(interval && interval.lower);
    const upper = finiteNumber(interval && interval.upper);
    if (lower === null || upper === null) return "";
    const boundaries = [
      interval.truncatedLower ? "下限触及正式估值边界，真实下界可能更低" : "",
      interval.truncatedUpper ? "上限触及正式估值边界，真实上界可能更高" : "",
    ].filter(Boolean);
    const range = `[${integer(lower)}, ${integer(upper)}]`;
    return boundaries.length ? `${range}（${boundaries.join("；")}）` : range;
  }

  function presentRating(input) {
    const rating = input && typeof input === "object" ? input : {};
    const estimate = finiteNumber(rating.estimate);
    const minimum = finiteNumber(rating.formalMinimum);
    const maximum = finiteNumber(rating.formalMaximum);
    const selected = finiteNumber(rating.selectedGameCount);
    const required = finiteNumber(rating.minimumGameCount);
    const status = String(rating.status || "").trim();
    const details = [];
    let value;

    if (status === "above_reference_range") {
      value = maximum === null ? "高于估值上限" : `高于 ${integer(maximum)}`;
      details.push(maximum === null
        ? "估值曲线到参考范围上限仍指向更高 Rating"
        : `估值曲线到正式上限 ${integer(maximum)} 仍指向更高 Rating，无法给出范围内点估计`);
    } else if (status === "below_reference_range") {
      value = minimum === null ? "低于估值下限" : `低于 ${integer(minimum)}`;
      details.push(minimum === null
        ? "估值曲线到参考范围下限仍指向更低 Rating"
        : `估值曲线到正式下限 ${integer(minimum)} 仍指向更低 Rating，无法给出范围内点估计`);
    } else if (status === "insufficient_target_games") {
      value = "样本不足";
      if (selected !== null && required !== null) {
        details.push(`可用于估值的完整对局 ${integer(selected)} 局，正式估值至少需要 ${integer(required)} 局`);
      } else {
        details.push("完整的近期有效对局少于正式估值要求");
      }
    } else if (estimate !== null) {
      value = integer(estimate);
    } else {
      value = STATUS_LABELS[status] || (status ? `无法估值（${status}）` : "未生成估值结果");
    }

    if (minimum !== null && maximum !== null) {
      details.push(`正式估值范围：${integer(minimum)}–${integer(maximum)}`);
    }

    const exclusions = Array.isArray(rating.excludedReasons) ? rating.excludedReasons : [];
    const exclusionParts = exclusions.map((item) => {
      const reason = String(item && item.reason || "").trim();
      const count = finiteNumber(item && item.count);
      if (!reason || count === null) return "";
      return `${EXCLUSION_LABELS[reason] || reason} ${integer(count)} 局`;
    }).filter(Boolean);
    const excludedCount = finiteNumber(rating.excludedGameCount);
    if (exclusionParts.length) {
      const countedExclusions = exclusions.reduce((sum, item) => {
        const itemCount = finiteNumber(item && item.count);
        return sum + (itemCount || 0);
      }, 0);
      details.push(`排除 ${integer(excludedCount === null ? countedExclusions : excludedCount)} 局：${exclusionParts.join("，")}`);
    }

    const intervals = Array.isArray(rating.intervals) ? rating.intervals.map(intervalText).filter(Boolean) : [];
    if (intervals.length) details.push(`95% 校准区间：${intervals.join("；")}`);
    if (status === "multiple_minima") details.push("估值曲线存在多个拟合程度接近的最低区域，点估计取其中的最佳网格点");

    const reasons = Array.isArray(rating.statusReasons) ? rating.statusReasons : [];
    const reasonDetails = reasons.map((reason) => {
      const key = String(reason || "").trim();
      return key ? (STATUS_REASON_LABELS[key] || `分析原因：${key}`) : "";
    }).filter(Boolean);
    for (const detail of reasonDetails) {
      if (!details.includes(detail)) details.push(detail);
    }

    return { value, detail: details.join("；"), statusLabel: STATUS_LABELS[status] || status };
  }

  const api = { presentRating };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  globalObject.PappRatingPresentation = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
