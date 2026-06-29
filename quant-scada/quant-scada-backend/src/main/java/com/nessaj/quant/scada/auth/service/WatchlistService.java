package com.nessaj.quant.scada.auth.service;

import com.nessaj.quant.scada.auth.dto.*;
import com.nessaj.quant.scada.auth.entity.UserWatchlist;
import com.nessaj.quant.scada.auth.mapper.StockBaseInfoMapper;
import com.nessaj.quant.scada.auth.mapper.UserWatchlistMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
public class WatchlistService {
    @Autowired
    private UserWatchlistMapper watchlistMapper;

    @Autowired
    private StockBaseInfoMapper stockBaseInfoMapper;

    public Map<String, Object> getPage(Long userId, int page, int size, String keyword) {
        int offset = page * size;
        List<WatchlistItemVO> items = watchlistMapper.findPageByUser(userId, keyword, offset, size);
        int total = watchlistMapper.countByUser(userId, keyword);

        Map<String, Object> result = new HashMap<>();
        result.put("content", items);
        result.put("totalElements", total);
        result.put("totalPages", (int) Math.ceil((double) total / size));
        result.put("number", page);
        result.put("size", size);
        return result;
    }

    public void add(Long userId, String stockCode) {
        if (watchlistMapper.findByUserAndCode(userId, stockCode) != null) {
            throw new RuntimeException("该股票已在自选列表中");
        }
        int maxOrder = watchlistMapper.maxSortOrder(userId);
        UserWatchlist wl = new UserWatchlist();
        wl.setUserId(userId);
        wl.setStockCode(stockCode);
        wl.setSortOrder(maxOrder + 1);
        wl.setCreatedAt(LocalDateTime.now());
        watchlistMapper.insert(wl);
    }

    public void delete(Long userId, Long id) {
        watchlistMapper.deleteById(id, userId);
    }

    public void reorder(ReorderRequest request) {
        if (request.getOrders() != null) {
            for (ReorderRequest.ReorderItem item : request.getOrders()) {
                watchlistMapper.updateSortOrder(item.getId(), item.getSortOrder());
            }
        }
    }

    public List<StockSearchVO> searchStocks(String keyword, String subMarket) {
        if (keyword == null || keyword.trim().isEmpty()) {
            return List.of();
        }
        return stockBaseInfoMapper.search(keyword.trim(), subMarket, 20);
    }
}
