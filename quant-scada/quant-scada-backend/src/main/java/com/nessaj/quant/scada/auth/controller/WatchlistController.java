package com.nessaj.quant.scada.auth.controller;

import com.nessaj.quant.scada.auth.dto.AddWatchlistRequest;
import com.nessaj.quant.scada.auth.dto.ReorderRequest;
import com.nessaj.quant.scada.auth.service.WatchlistService;
import com.nessaj.quant.scada.common.CommonResult;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.bind.annotation.*;

import javax.validation.Valid;

@RestController
@RequestMapping("/api")
public class WatchlistController {
    @Autowired
    private WatchlistService watchlistService;

    private Long getUserId() {
        return (Long) SecurityContextHolder.getContext().getAuthentication().getPrincipal();
    }

    @GetMapping("/watchlist")
    public CommonResult getWatchlist(@RequestParam(defaultValue = "0") int page,
                          @RequestParam(defaultValue = "20") int size,
                          @RequestParam(required = false) String keyword) {
        return CommonResult.ok(watchlistService.getPage(getUserId(), page, size, keyword));
    }

    @PostMapping("/watchlist")
    public CommonResult addWatchlist(@Valid @RequestBody AddWatchlistRequest request) {
        watchlistService.add(getUserId(), request.getStockCode());
        return CommonResult.ok("添加成功");
    }

    @DeleteMapping("/watchlist/{id}")
    public CommonResult deleteWatchlist(@PathVariable Long id) {
        watchlistService.delete(getUserId(), id);
        return CommonResult.ok("删除成功");
    }

    @PutMapping("/watchlist/reorder")
    public CommonResult reorder(@RequestBody ReorderRequest request) {
        watchlistService.reorder(request);
        return CommonResult.ok("排序更新成功");
    }

    @GetMapping("/stock/search")
    public CommonResult searchStocks(@RequestParam String keyword,
                                     @RequestParam(required = false) String subMarket) {
        return CommonResult.ok(watchlistService.searchStocks(keyword, subMarket));
    }
}
