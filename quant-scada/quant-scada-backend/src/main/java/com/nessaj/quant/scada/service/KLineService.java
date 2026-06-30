package com.nessaj.quant.scada.service;

import com.baomidou.dynamic.datasource.annotation.DS;
import com.nessaj.quant.scada.dto.KLineVO;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import javax.sql.DataSource;
import java.sql.*;
import java.util.ArrayList;
import java.util.List;

@Service
public class KLineService {

    @Autowired
    private DataSource dataSource;

    private static final String HIST_DB = "quant_scada_hist";
    private static final List<String> VALID_PERIODS = List.of("1m", "5m", "15m");

    /**
     * 查询历史K线
     * @param code  股票代码 (如 000001)
     * @param period 周期: 1m / 5m / 15m
     * @param start  开始时间 (格式 yyyy-MM-dd HH:mm:ss)
     * @param end    结束时间
     * @param limit  返回条数 (默认240)
     */
    @DS("tdengine")
    public List<KLineVO> queryKline(String code, String period, String start, String end, int limit) {
        if (!VALID_PERIODS.contains(period)) {
            period = "1m";
        }
        if (limit <= 0) limit = 240;

        String tableName = HIST_DB + ".hk_" + code;
        String sql = buildSql(tableName, period, start, end, limit);

        List<KLineVO> result = new ArrayList<>();
        try (Connection conn = dataSource.getConnection();
             Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery(sql)) {

            while (rs.next()) {
                KLineVO vo = new KLineVO();
                vo.setTs(rs.getString("ts"));
                vo.setOpen(rs.getDouble("open"));
                vo.setHigh(rs.getDouble("high"));
                vo.setLow(rs.getDouble("low"));
                vo.setClose(rs.getDouble("close"));
                vo.setVolume(rs.getLong("volume"));
                vo.setAmount(rs.getDouble("amount"));
                result.add(vo);
            }
        } catch (Exception e) {
            throw new RuntimeException("K线查询失败", e);
        }

        // 降序查询时反转
        if (result.size() > 1 && result.get(0).getTs().compareTo(result.get(result.size() - 1).getTs()) > 0) {
            java.util.Collections.reverse(result);
        }

        return result;
    }

    @DS("tdengine")
    public List<String> availableDates(String code) {
        String sql = "SELECT ts FROM " + HIST_DB + ".hk_" + code + " ORDER BY ts ASC";
        java.util.LinkedHashSet<String> dates = new java.util.LinkedHashSet<>();
        try (Connection conn = dataSource.getConnection();
             Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery(sql)) {
            while (rs.next()) {
                String ts = rs.getString("ts");
                if (ts != null && ts.length() >= 10) {
                    dates.add(ts.substring(0, 10));
                }
            }
        } catch (Exception e) {
            throw new RuntimeException("交易日查询失败", e);
        }
        List<String> result = new ArrayList<>(dates);
        java.util.Collections.sort(result, java.util.Collections.reverseOrder());
        return result;
    }

    private String buildSql(String table, String period, String start, String end, int limit) {
        StringBuilder sb = new StringBuilder();
        String tsCol = "ts";

        if ("1m".equals(period)) {
            sb.append("SELECT ts, open, high, low, close, volume, amount FROM ").append(table);
        } else {
            // 5m / 15m 走 INTERVAL 降采样
            int minutes = "5m".equals(period) ? 5 : 15;
            sb.append("SELECT _wstart AS ts, FIRST(open) AS open, MAX(high) AS high, ")
              .append("MIN(low) AS low, LAST(close) AS close, SUM(volume) AS volume, SUM(amount) AS amount ")
              .append("FROM ").append(table);
            tsCol = "_wstart";
        }

        // WHERE
        boolean hasWhere = false;
        if (start != null && !start.isEmpty()) {
            sb.append(" WHERE ts >= '").append(start).append("'");
            hasWhere = true;
        }
        if (end != null && !end.isEmpty()) {
            sb.append(hasWhere ? " AND " : " WHERE ").append("ts <= '").append(end).append("'");
            hasWhere = true;
        }

        // INTERVAL
        if (!"1m".equals(period)) {
            int minutes = "5m".equals(period) ? 5 : 15;
            sb.append(" INTERVAL(").append(minutes).append("m)");
        }

        // 默认取最新 N 条: 先降序取再反转
        if ((start == null || start.isEmpty()) && (end == null || end.isEmpty())) {
            sb.append(" ORDER BY ").append(tsCol).append(" DESC LIMIT ").append(limit);
        } else {
            sb.append(" ORDER BY ").append(tsCol).append(" ASC");
            if (limit > 0) {
                sb.append(" LIMIT ").append(limit);
            }
        }

        return sb.toString();
    }
}
