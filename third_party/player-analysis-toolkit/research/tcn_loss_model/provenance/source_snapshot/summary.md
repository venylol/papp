# tcn_board_cnn_time_model

- data: `oq_reversi_5min_elo2000_hints\position_hints.csv`
- context metadata: `oq_reversi_5min_elo2000_hints\position_context_metadata.csv`
- feature revision: `side_history_phase_board_hint6_close_best_pattern_hint6_dispersion_compact_dispersion_human_opening_frequency_context_metadata_direct_seconds_board_cnn_conditioned_tcn_current_hint6_rank_planes_rank1_score_rank2_4_gap_planes_prev_own_hint1_value`
- target: `log1p(actual_thinking_time_ms / 1000.0)`
- predicted seconds: `expm1(pred_log)`, clamped to `[0.05, remaining_before_s * 0.95]`
- model: `board-conditioned causal TCN with 8x8 CNN board encoder`
- device: `cuda`
- rows used: `371801`
- train rows: `334191`
- test rows: `37610`
- train games: `8273`
- test games: `920`
- max seq len: `66`
- input numeric features: `362`
- board CNN channels: `23`
- current hint6 rank-specific planes: `6`
- current hint6 value planes: `4`
- prev own hint6_1 value planes: `2`
- best epoch: `596`
- best checkpoint: `tcn_board_cnn_time_model_outputs\tcn_board_cnn_time_model_best.pt`
- latest checkpoint: `tcn_board_cnn_time_model_outputs\tcn_board_cnn_time_model_latest.pt`

## Metrics
| metric | value |
| --- | --- |
| MAE_seconds | 2.7863188959539595 |
| Median_AE_seconds | 0.9665357761383058 |
| RMSE_log | 0.46739816859319466 |
| R2_seconds | 0.5738383774085944 |
| R2_log | 0.7371656224888132 |
| Spearman_pred_seconds_vs_actual_seconds | 0.8695242798359644 |
| Actual_mean_seconds | 6.133408348843393 |
| Predicted_mean_seconds | 5.6454438008344106 |
| Actual_median_seconds | 2.6125 |
| Predicted_median_seconds | 3.0866477489471436 |
| n_test | 37610.0 |
| n_train | 334191 |
| train_games | 8273 |
| test_games | 920 |
| max_seq_len | 66 |
| input_features | 362 |
| board_contexts_per_position | 3 |
| board_cnn_channels | 23 |
| historical_move_planes_per_position | 2 |
| current_hint_rank_planes_per_position | 6 |
| current_hint_value_planes_per_position | 4 |
| prev_own_hint_value_planes_per_position | 2 |
| numeric_base_features | 256 |
| metadata_numeric_features | 61 |
| excluded_categorical_features | 7 |
| best_epoch | 596 |
| eval_checkpoint | tcn_board_cnn_time_model_outputs\tcn_board_cnn_time_model.pt |
| device_cuda | 1 |