package com.nessaj.quant.scada.controller;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/health")
public class HealthCheck {

    @GetMapping("check")
    public String healthcheck(){
        return "the quant-scada-sim service is ok.";
    }

}
