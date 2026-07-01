package com.nessaj.quant.scada.controller;

import com.nessaj.quant.scada.common.CommonResult;
import com.nessaj.quant.scada.dto.KLineVO;
import com.nessaj.quant.scada.service.KLineService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/market")
public class KLineController {

    @Autowired
    private KLineService klineService;

    @GetMapping("/kline")
    public CommonResult getKline(@RequestParam String code,
                                  @RequestParam(defaultValue = "SH") String market,
                                  @RequestParam(defaultValue = "1m") String period,
                                  @RequestParam(required = false) String start,
                                  @RequestParam(required = false) String end,
                                  @RequestParam(defaultValue = "240") int limit) {
        List<KLineVO> list = klineService.queryKline(code, market, period, start, end, limit);
        return CommonResult.ok(list);
    }

    @GetMapping("/kline/dates")
    public CommonResult getKlineDates(@RequestParam String code,
                                       @RequestParam(defaultValue = "SH") String market) {
        List<String> dates = klineService.availableDates(code, market);
        return CommonResult.ok(dates);
    }
}
